import sqlite3
from datetime import datetime
from pathlib import Path

from backend.app.config import BACKUP_DIR, DATABASE_PATH
from backend.app.services.cinema import seat_index


DATABASE_SCHEMA_VERSION = 3


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database():
    database_existed = Path(DATABASE_PATH).exists()
    connection = get_connection()
    try:
        legacy_ticket_seats = _has_column(connection, "tickets", "seats")
        if database_existed and legacy_ticket_seats:
            from backend.app.backup import create_backup_from_connection

            create_backup_from_connection(
                connection,
                BACKUP_DIR,
                prefix="pre-seat-migration",
            )

        connection.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS user_sessions_expiry
            ON user_sessions(expires_at);
        CREATE TABLE IF NOT EXISTS credential_bootstrap(
            username TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL UNIQUE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS movies(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            genre TEXT NOT NULL,
            duration INTEGER NOT NULL CHECK(duration > 0),
            age TEXT DEFAULT 'T13',
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'Đang chiếu'
        );
        CREATE TABLE IF NOT EXISTS rooms(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            capacity INTEGER NOT NULL CHECK(capacity > 0)
        );
        CREATE TABLE IF NOT EXISTS showtimes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movie_id INTEGER NOT NULL,
            room_id INTEGER NOT NULL,
            show_date TEXT NOT NULL,
            show_time TEXT NOT NULL,
            price REAL NOT NULL CHECK(price > 0),
            FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE,
            FOREIGN KEY(room_id) REFERENCES rooms(id)
        );
        CREATE TABLE IF NOT EXISTS tickets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            showtime_id INTEGER NOT NULL,
            customer_name TEXT NOT NULL,
            total REAL NOT NULL CHECK(total >= 0),
            status TEXT DEFAULT 'Đã thanh toán',
            created_at TEXT NOT NULL,
            FOREIGN KEY(showtime_id) REFERENCES showtimes(id)
        );
        CREATE TABLE IF NOT EXISTS ticket_seats(
            ticket_id INTEGER NOT NULL,
            showtime_id INTEGER NOT NULL,
            seat_index INTEGER NOT NULL CHECK(seat_index > 0),
            PRIMARY KEY(showtime_id, seat_index),
            UNIQUE(ticket_id, seat_index),
            FOREIGN KEY(ticket_id, showtime_id)
                REFERENCES tickets(id, showtime_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS audit_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT,
            created_at TEXT NOT NULL
        );
        """)

        from backend.app.security import configure_bootstrap_accounts

        configure_bootstrap_accounts(connection)

        if connection.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 0:
            movies = [
                ("Dune: Part Two", "Khoa học viễn tưởng", 166, "T13",
                 "Paul Atreides đồng hành cùng Chani và người Fremen trên hành trình báo thù.", "Đang chiếu"),
                ("Inside Out 2", "Hoạt hình", 96, "P",
                 "Riley bước vào tuổi thiếu niên và đối diện với những cảm xúc mới.", "Đang chiếu"),
                ("The Batman", "Hành động", 176, "T16",
                 "Batman điều tra một chuỗi vụ án bí ẩn tại Gotham.", "Sắp chiếu"),
                ("Avatar: The Way of Water", "Phiêu lưu", 192, "T13",
                 "Gia đình Sully tìm nơi trú ẩn mới và bảo vệ đại dương Pandora.", "Đang chiếu"),
            ]
            connection.executemany(
                """INSERT INTO movies(title,genre,duration,age,description,status)
                   VALUES(?,?,?,?,?,?)""",
                movies,
            )

        if connection.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0:
            connection.executemany(
                "INSERT INTO rooms(name,capacity) VALUES(?,?)",
                [("Phòng 01", 60), ("Phòng 02", 80), ("Phòng VIP", 40)],
            )

        if connection.execute("SELECT COUNT(*) FROM showtimes").fetchone()[0] == 0:
            movie_ids = [
                row["id"] for row in
                connection.execute("SELECT id FROM movies ORDER BY id")
            ]
            room_ids = [
                row["id"] for row in
                connection.execute("SELECT id FROM rooms ORDER BY id")
            ]
            today = datetime.now().strftime("%Y-%m-%d")
            times = ["10:00", "13:30", "16:30", "19:30"]
            showtimes = [
                (movie_id, room_ids[index % len(room_ids)], today,
                 times[index], 75000 + index * 5000)
                for index, movie_id in enumerate(movie_ids)
            ]
            connection.executemany(
                """INSERT INTO showtimes(movie_id,room_id,show_date,show_time,price)
                   VALUES(?,?,?,?,?)""",
                showtimes,
            )

        _ensure_ticket_parent_key(connection)
        if legacy_ticket_seats:
            _migrate_ticket_seats(connection)

        if connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0:
            showtime = connection.execute(
                "SELECT id FROM showtimes ORDER BY id LIMIT 1"
            ).fetchone()
            if showtime:
                cursor = connection.execute(
                    """INSERT INTO tickets
                       (showtime_id,customer_name,total,status,created_at)
                       VALUES(?,?,?,?,?)""",
                    (
                        showtime["id"],
                        "Nguyễn Văn A",
                        150000,
                        "Đã thanh toán",
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                connection.executemany(
                    """INSERT INTO ticket_seats(ticket_id,showtime_id,seat_index)
                       VALUES(?,?,?)""",
                    [(cursor.lastrowid, showtime["id"], index) for index in (1, 2)],
                )
        _install_integrity_constraints(connection)
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.commit()
    finally:
        connection.close()


def _has_column(connection, table, column):
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return False
    return any(
        row["name"] == column
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    )


def _ensure_ticket_parent_key(connection):
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_id_showtime
           ON tickets(id,showtime_id)"""
    )


def _migrate_ticket_seats(connection):
    rows = connection.execute("""
        SELECT t.id,t.showtime_id,t.seats,r.capacity
        FROM tickets t
        JOIN showtimes s ON s.id=t.showtime_id
        JOIN rooms r ON r.id=s.room_id
        WHERE t.status='Đã thanh toán'
        ORDER BY t.id
    """).fetchall()
    for row in rows:
        seats = [seat.strip().upper() for seat in row["seats"].split(",") if seat.strip()]
        if len(set(seats)) != len(seats):
            raise RuntimeError(
                f"Không thể nâng cấp CSDL: vé #{row['id']} có ghế bị lặp."
            )
        indices = [seat_index(seat, row["capacity"]) for seat in seats]
        if any(index is None for index in indices):
            raise RuntimeError(
                f"Không thể nâng cấp CSDL: vé #{row['id']} có ghế ngoài sức chứa phòng."
            )
        try:
            connection.executemany(
                """INSERT INTO ticket_seats(ticket_id,showtime_id,seat_index)
                   VALUES(?,?,?)""",
                [
                    (row["id"], row["showtime_id"], index)
                    for index in indices
                ],
            )
        except sqlite3.IntegrityError as exc:
            raise RuntimeError(
                "Không thể nâng cấp CSDL: dữ liệu vé hiện có chứa ghế "
                "đã bị đặt trùng giữa các vé."
            ) from exc

    connection.execute("ALTER TABLE tickets DROP COLUMN seats")


def _install_integrity_constraints(connection):
    _validate_existing_data(connection)
    connection.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_showtimes_room_date_time
        ON showtimes(room_id,show_date,show_time)
    """)
    _install_integrity_triggers(connection)


def _validate_existing_data(connection):
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise RuntimeError(
            "Không thể áp dụng ràng buộc: CSDL hiện có quan hệ khóa ngoại bị lỗi."
        )
    invalid_seat = connection.execute("""
        SELECT ts.ticket_id,ts.seat_index,r.capacity
        FROM ticket_seats ts
        JOIN showtimes s ON s.id=ts.showtime_id
        JOIN rooms r ON r.id=s.room_id
        WHERE typeof(ts.seat_index)<>'integer' OR ts.seat_index>r.capacity
        LIMIT 1
    """).fetchone()
    if invalid_seat:
        raise RuntimeError(
            f"Không thể áp dụng ràng buộc: vé #{invalid_seat['ticket_id']} "
            f"có ghế vượt sức chứa {invalid_seat['capacity']}."
        )
    invalid_room = connection.execute(
        "SELECT id FROM rooms WHERE typeof(capacity)<>'integer' "
        "OR capacity<=0 LIMIT 1"
    ).fetchone()
    if invalid_room:
        raise RuntimeError(
            f"Không thể áp dụng ràng buộc: phòng #{invalid_room['id']} "
            "có sức chứa không hợp lệ."
        )
    invalid_movie = connection.execute(
        "SELECT id FROM movies WHERE typeof(duration)<>'integer' "
        "OR duration<=0 LIMIT 1"
    ).fetchone()
    if invalid_movie:
        raise RuntimeError(
            f"Không thể áp dụng ràng buộc: phim #{invalid_movie['id']} "
            "có thời lượng không hợp lệ."
        )
    invalid_showtime = connection.execute("""
        SELECT id FROM showtimes
        WHERE strftime('%Y-%m-%d', show_date) IS NULL
           OR strftime('%Y-%m-%d', show_date)<>show_date
           OR strftime('%Y-%m-%d', show_date, '+0 days')<>show_date
           OR strftime('%H:%M', '2000-01-01 ' || show_time) IS NULL
           OR strftime('%H:%M', '2000-01-01 ' || show_time)<>show_time
           OR typeof(price) NOT IN ('integer','real')
           OR price<=0 OR price>=1.0e15
        LIMIT 1
    """).fetchone()
    if invalid_showtime:
        raise RuntimeError(
            "Không thể áp dụng ràng buộc: suất chiếu "
            f"#{invalid_showtime['id']} có ngày, giờ hoặc giá vé không hợp lệ."
        )
    invalid_ticket = connection.execute("""
        SELECT id FROM tickets
        WHERE trim(customer_name)='' OR typeof(total) NOT IN ('integer','real')
           OR total<0 OR total>=1.0e15
        LIMIT 1
    """).fetchone()
    if invalid_ticket:
        raise RuntimeError(
            "Không thể áp dụng ràng buộc: vé "
            f"#{invalid_ticket['id']} có khách hàng hoặc tổng tiền không hợp lệ."
        )
    overlap = connection.execute("""
        SELECT first.id first_id,second.id second_id
        FROM showtimes first
        JOIN movies first_movie ON first_movie.id=first.movie_id
        JOIN showtimes second ON second.room_id=first.room_id
            AND second.show_date=first.show_date AND second.id>first.id
        JOIN movies second_movie ON second_movie.id=second.movie_id
        WHERE julianday(first.show_date || ' ' || first.show_time)
                < julianday(second.show_date || ' ' || second.show_time,
                            printf('+%d minutes', second_movie.duration))
          AND julianday(second.show_date || ' ' || second.show_time)
                < julianday(first.show_date || ' ' || first.show_time,
                            printf('+%d minutes', first_movie.duration))
        LIMIT 1
    """).fetchone()
    if overlap:
        raise RuntimeError(
            "Không thể áp dụng ràng buộc: suất chiếu "
            f"#{overlap['first_id']} và #{overlap['second_id']} đang bị chồng lấn."
        )


def _install_integrity_triggers(connection):
    connection.executescript("""
    DROP TRIGGER IF EXISTS ticket_seats_capacity_insert;
    DROP TRIGGER IF EXISTS ticket_seats_capacity_update;
    DROP TRIGGER IF EXISTS tickets_validate_insert;
    DROP TRIGGER IF EXISTS tickets_validate_update;
    DROP TRIGGER IF EXISTS showtimes_validate_insert;
    DROP TRIGGER IF EXISTS showtimes_validate_update;
    DROP TRIGGER IF EXISTS movies_validate_duration_update;
    DROP TRIGGER IF EXISTS movies_validate_duration_insert;
    DROP TRIGGER IF EXISTS rooms_are_fixed_update;
    DROP TRIGGER IF EXISTS rooms_are_fixed_delete;
    DROP TRIGGER IF EXISTS rooms_are_fixed_insert;

    CREATE TRIGGER IF NOT EXISTS ticket_seats_capacity_insert
    BEFORE INSERT ON ticket_seats
    BEGIN
        SELECT CASE WHEN typeof(NEW.seat_index)<>'integer' OR NEW.seat_index > (
            SELECT r.capacity
            FROM showtimes s JOIN rooms r ON r.id=s.room_id
            WHERE s.id=NEW.showtime_id
        ) THEN RAISE(ABORT, 'Seat exceeds room capacity') END;
    END;

    CREATE TRIGGER IF NOT EXISTS ticket_seats_capacity_update
    BEFORE UPDATE OF showtime_id,seat_index ON ticket_seats
    BEGIN
        SELECT CASE WHEN typeof(NEW.seat_index)<>'integer' OR NEW.seat_index > (
            SELECT r.capacity
            FROM showtimes s JOIN rooms r ON r.id=s.room_id
            WHERE s.id=NEW.showtime_id
        ) THEN RAISE(ABORT, 'Seat exceeds room capacity') END;
    END;

    CREATE TRIGGER IF NOT EXISTS tickets_validate_insert
    BEFORE INSERT ON tickets
    BEGIN
        SELECT CASE WHEN trim(NEW.customer_name) = ''
            THEN RAISE(ABORT, 'Customer name is required') END;
        SELECT CASE WHEN typeof(NEW.total) NOT IN ('integer','real')
            OR NEW.total < 0 OR NEW.total >= 1.0e15
            THEN RAISE(ABORT, 'Invalid ticket total') END;
    END;

    CREATE TRIGGER IF NOT EXISTS tickets_validate_update
    BEFORE UPDATE OF showtime_id,customer_name,total,status ON tickets
    BEGIN
        SELECT CASE WHEN trim(NEW.customer_name) = ''
            THEN RAISE(ABORT, 'Customer name is required') END;
        SELECT CASE WHEN typeof(NEW.total) NOT IN ('integer','real')
            OR NEW.total < 0 OR NEW.total >= 1.0e15
            THEN RAISE(ABORT, 'Invalid ticket total') END;
        SELECT CASE WHEN NEW.showtime_id<>OLD.showtime_id
            AND EXISTS (SELECT 1 FROM ticket_seats WHERE ticket_id=OLD.id)
            THEN RAISE(ABORT, 'Cannot move a ticket with booked seats') END;
    END;

    CREATE TRIGGER IF NOT EXISTS showtimes_validate_insert
    BEFORE INSERT ON showtimes
    BEGIN
        SELECT CASE WHEN strftime('%Y-%m-%d', NEW.show_date) IS NULL
            OR strftime('%Y-%m-%d', NEW.show_date) <> NEW.show_date
            OR strftime('%Y-%m-%d', NEW.show_date, '+0 days') <> NEW.show_date
            THEN RAISE(ABORT, 'Invalid show date') END;
        SELECT CASE WHEN strftime('%H:%M', '2000-01-01 ' || NEW.show_time) IS NULL
            OR strftime('%H:%M', '2000-01-01 ' || NEW.show_time) <> NEW.show_time
            THEN RAISE(ABORT, 'Invalid show time') END;
        SELECT CASE WHEN typeof(NEW.price) NOT IN ('integer','real')
            OR NEW.price <= 0 OR NEW.price >= 1.0e15
            THEN RAISE(ABORT, 'Invalid ticket price') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1
            FROM showtimes existing
            JOIN movies existing_movie ON existing_movie.id=existing.movie_id
            JOIN movies new_movie ON new_movie.id=NEW.movie_id
            WHERE existing.room_id=NEW.room_id
              AND julianday(NEW.show_date || ' ' || NEW.show_time)
                    < julianday(existing.show_date || ' ' || existing.show_time,
                                printf('+%d minutes', existing_movie.duration))
              AND julianday(existing.show_date || ' ' || existing.show_time)
                    < julianday(NEW.show_date || ' ' || NEW.show_time,
                                printf('+%d minutes', new_movie.duration))
        ) THEN RAISE(ABORT, 'Showtime overlaps another show in this room') END;
    END;

    CREATE TRIGGER IF NOT EXISTS showtimes_validate_update
    BEFORE UPDATE OF movie_id,room_id,show_date,show_time,price ON showtimes
    BEGIN
        SELECT CASE WHEN strftime('%Y-%m-%d', NEW.show_date) IS NULL
            OR strftime('%Y-%m-%d', NEW.show_date) <> NEW.show_date
            OR strftime('%Y-%m-%d', NEW.show_date, '+0 days') <> NEW.show_date
            THEN RAISE(ABORT, 'Invalid show date') END;
        SELECT CASE WHEN strftime('%H:%M', '2000-01-01 ' || NEW.show_time) IS NULL
            OR strftime('%H:%M', '2000-01-01 ' || NEW.show_time) <> NEW.show_time
            THEN RAISE(ABORT, 'Invalid show time') END;
        SELECT CASE WHEN typeof(NEW.price) NOT IN ('integer','real')
            OR NEW.price <= 0 OR NEW.price >= 1.0e15
            THEN RAISE(ABORT, 'Invalid ticket price') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1
            FROM showtimes existing
            JOIN movies existing_movie ON existing_movie.id=existing.movie_id
            JOIN movies new_movie ON new_movie.id=NEW.movie_id
            WHERE existing.id<>NEW.id
              AND existing.room_id=NEW.room_id
              AND julianday(NEW.show_date || ' ' || NEW.show_time)
                    < julianday(existing.show_date || ' ' || existing.show_time,
                                printf('+%d minutes', existing_movie.duration))
              AND julianday(existing.show_date || ' ' || existing.show_time)
                    < julianday(NEW.show_date || ' ' || NEW.show_time,
                                printf('+%d minutes', new_movie.duration))
        ) THEN RAISE(ABORT, 'Showtime overlaps another show in this room') END;
    END;

    CREATE TRIGGER IF NOT EXISTS movies_validate_duration_update
    BEFORE UPDATE OF duration ON movies
    BEGIN
        SELECT CASE WHEN typeof(NEW.duration)<>'integer' OR NEW.duration <= 0
            THEN RAISE(ABORT, 'Movie duration must be positive') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1
            FROM showtimes changed
            JOIN showtimes existing ON existing.room_id=changed.room_id
                AND existing.id<>changed.id
            JOIN movies existing_movie ON existing_movie.id=existing.movie_id
            WHERE changed.movie_id=NEW.id
              AND julianday(changed.show_date || ' ' || changed.show_time)
                    < julianday(existing.show_date || ' ' || existing.show_time,
                                printf('+%d minutes', existing_movie.duration))
              AND julianday(existing.show_date || ' ' || existing.show_time)
                    < julianday(changed.show_date || ' ' || changed.show_time,
                                printf('+%d minutes', NEW.duration))
        ) THEN RAISE(ABORT, 'Movie duration creates an overlapping showtime') END;
    END;

    CREATE TRIGGER IF NOT EXISTS movies_validate_duration_insert
    BEFORE INSERT ON movies
    BEGIN
        SELECT CASE WHEN typeof(NEW.duration)<>'integer' OR NEW.duration <= 0
            THEN RAISE(ABORT, 'Movie duration must be positive') END;
    END;

    CREATE TRIGGER IF NOT EXISTS rooms_are_fixed_update
    BEFORE UPDATE ON rooms
    BEGIN
        SELECT RAISE(ABORT, 'Room configuration is fixed');
    END;

    CREATE TRIGGER IF NOT EXISTS rooms_are_fixed_delete
    BEFORE DELETE ON rooms
    BEGIN
        SELECT RAISE(ABORT, 'Room configuration is fixed');
    END;

    CREATE TRIGGER IF NOT EXISTS rooms_are_fixed_insert
    BEFORE INSERT ON rooms
    BEGIN
        SELECT RAISE(ABORT, 'Room configuration is fixed');
    END;
    """)


def list_movies(query=""):
    connection = get_connection()
    try:
        if query:
            rows = connection.execute(
                """SELECT * FROM movies
                   WHERE title LIKE ? OR genre LIKE ? ORDER BY id DESC""",
                (f"%{query}%", f"%{query}%"),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM movies ORDER BY id DESC"
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def list_rooms():
    connection = get_connection()
    try:
        return [
            dict(row) for row in connection.execute(
                "SELECT id,name,capacity FROM rooms ORDER BY id"
            ).fetchall()
        ]
    finally:
        connection.close()


def dashboard_data():
    connection = get_connection()
    try:
        movie_count = connection.execute(
            "SELECT COUNT(*) FROM movies"
        ).fetchone()[0]
        ticket_count = connection.execute(
            "SELECT COUNT(*) FROM tickets WHERE status='Đã thanh toán'"
        ).fetchone()[0]
        revenue = connection.execute(
            "SELECT COALESCE(SUM(total),0) FROM tickets WHERE status='Đã thanh toán'"
        ).fetchone()[0]
        show_count = connection.execute(
            "SELECT COUNT(*) FROM showtimes"
        ).fetchone()[0]
        revenue_rows = connection.execute("""
            SELECT m.title, COALESCE(SUM(t.total),0) revenue
            FROM movies m
            LEFT JOIN showtimes s ON s.movie_id=m.id
            LEFT JOIN tickets t ON t.showtime_id=s.id AND t.status='Đã thanh toán'
            GROUP BY m.id ORDER BY revenue DESC
        """).fetchall()
        return {
            "movie_count": movie_count,
            "ticket_count": ticket_count,
            "revenue": revenue,
            "show_count": show_count,
            "chart_labels": [row["title"] for row in revenue_rows],
            "chart_values": [row["revenue"] for row in revenue_rows],
        }
    finally:
        connection.close()


def list_showtimes():
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT s.id,s.movie_id,s.room_id,m.title,r.name room,r.capacity,
                   m.duration,s.show_date,s.show_time,s.price
            FROM showtimes s
            JOIN movies m ON m.id=s.movie_id
            JOIN rooms r ON r.id=s.room_id
            ORDER BY s.show_date,s.show_time
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def list_rooms_with_show_count():
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT r.id,r.name,r.capacity,COUNT(DISTINCT s.id) show_count
            FROM rooms r LEFT JOIN showtimes s ON s.room_id=r.id
            GROUP BY r.id ORDER BY r.id
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def showtime_seat_status(showtime_id):
    connection = get_connection()
    try:
        show = connection.execute("""
            SELECT s.id,r.capacity FROM showtimes s
            JOIN rooms r ON r.id=s.room_id WHERE s.id=?
        """, (showtime_id,)).fetchone()
        if not show:
            return None
        rows = connection.execute("""
            SELECT seat_index FROM ticket_seats
            WHERE showtime_id=? ORDER BY seat_index
        """, (showtime_id,)).fetchall()
        from backend.app.services.cinema import seat_name

        sold = sorted({
            seat_name(row["seat_index"], show["capacity"])
            for row in rows
        })
        return {"capacity": show["capacity"], "sold": sold}
    finally:
        connection.close()
