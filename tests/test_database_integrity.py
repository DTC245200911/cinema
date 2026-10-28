import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.app import database
from backend.app.backup import create_backup, restore_database, validate_backup


class DatabaseIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_database_path = database.DATABASE_PATH
        database.DATABASE_PATH = self.root / "cinema.db"
        database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def test_database_rejects_duplicate_seats_and_seats_over_capacity(self):
        connection = database.get_connection()
        try:
            ticket_id = connection.execute(
                """INSERT INTO tickets
                   (showtime_id,customer_name,total,status,created_at)
                   VALUES(1,'Duplicate-seat test',75000,'Đã thanh toán','test')"""
            ).lastrowid
            connection.execute(
                "INSERT INTO ticket_seats(ticket_id,showtime_id,seat_index) VALUES(?,?,?)",
                (ticket_id, 1, 3),
            )
            duplicate_ticket = connection.execute(
                """INSERT INTO tickets
                   (showtime_id,customer_name,total,status,created_at)
                   VALUES(1,'Duplicate-seat test 2',75000,'Đã thanh toán','test')"""
            ).lastrowid
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO ticket_seats(ticket_id,showtime_id,seat_index)
                       VALUES(?,?,?)""",
                    (duplicate_ticket, 1, 3),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "capacity"):
                connection.execute(
                    """INSERT INTO ticket_seats(ticket_id,showtime_id,seat_index)
                       VALUES(?,?,?)""",
                    (ticket_id, 1, 61),
                )
        finally:
            connection.close()

    def test_database_rejects_malformed_dates_and_non_integer_seats(self):
        connection = database.get_connection()
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "Invalid show date"):
                connection.execute(
                    """INSERT INTO showtimes
                       (movie_id,room_id,show_date,show_time,price)
                       VALUES(1,1,'2099-02-31','10:00',80000)"""
                )
            ticket_id = connection.execute(
                """INSERT INTO tickets
                   (showtime_id,customer_name,total,status,created_at)
                   VALUES(1,'Fractional-seat test',75000,'Đã thanh toán','test')"""
            ).lastrowid
            with self.assertRaisesRegex(sqlite3.IntegrityError, "capacity"):
                connection.execute(
                    """INSERT INTO ticket_seats(ticket_id,showtime_id,seat_index)
                       VALUES(?,?,?)""",
                    (ticket_id, 1, 3.5),
                )
        finally:
            connection.close()

    def test_database_rejects_overlapping_showtimes(self):
        connection = database.get_connection()
        try:
            connection.execute(
                """INSERT INTO showtimes
                   (movie_id,room_id,show_date,show_time,price)
                   VALUES(1,1,'2099-01-01','10:00',80000)"""
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "overlaps"):
                connection.execute(
                    """INSERT INTO showtimes
                       (movie_id,room_id,show_date,show_time,price)
                       VALUES(2,1,'2099-01-01','12:00',80000)"""
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO showtimes
                       (movie_id,room_id,show_date,show_time,price)
                       VALUES(2,1,'2099-01-01','10:00',80000)"""
                )
        finally:
            connection.close()

    def test_database_rejects_duration_change_that_overlaps_schedule(self):
        connection = database.get_connection()
        try:
            connection.execute(
                """INSERT INTO showtimes
                   (movie_id,room_id,show_date,show_time,price)
                   VALUES(1,1,'2099-01-01','10:00',80000)"""
            )
            connection.execute(
                """INSERT INTO showtimes
                   (movie_id,room_id,show_date,show_time,price)
                   VALUES(2,1,'2099-01-01','13:30',80000)"""
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "overlapping"):
                connection.execute("UPDATE movies SET duration=211 WHERE id=1")
        finally:
            connection.close()

    def test_database_prevents_room_configuration_changes(self):
        connection = database.get_connection()
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "fixed"):
                connection.execute(
                    "UPDATE rooms SET capacity=61 WHERE id=1"
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "fixed"):
                connection.execute("DELETE FROM rooms WHERE id=1")
        finally:
            connection.close()

    def test_backup_restore_validates_and_preserves_a_safety_backup(self):
        backup_dir = self.root / "backups"
        backup = create_backup(database.DATABASE_PATH, backup_dir)
        validate_backup(backup)

        connection = database.get_connection()
        try:
            connection.execute("UPDATE movies SET title='Changed' WHERE id=1")
            connection.commit()
        finally:
            connection.close()

        safety_backup = restore_database(
            backup,
            database_path=database.DATABASE_PATH,
            directory=backup_dir,
        )
        self.assertIsNotNone(safety_backup)
        self.assertTrue(safety_backup.is_file())
        self.assertEqual(database.list_movies("Dune")[0]["title"], "Dune: Part Two")
        check = sqlite3.connect(database.DATABASE_PATH)
        try:
            self.assertEqual(check.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            check.close()

    def test_backup_validation_rejects_non_sqlite_files(self):
        invalid_file = self.root / "not-a-database.sqlite3"
        invalid_file.write_text("not sqlite", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SQLite"):
            validate_backup(invalid_file)

    def test_legacy_seat_column_migrates_with_pre_migration_backup(self):
        legacy_path = self.root / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript("""
            CREATE TABLE users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL
            );
            CREATE TABLE movies(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                genre TEXT NOT NULL,
                duration INTEGER NOT NULL,
                age TEXT DEFAULT 'T13',
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'Đang chiếu'
            );
            CREATE TABLE rooms(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                capacity INTEGER NOT NULL
            );
            CREATE TABLE showtimes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_id INTEGER NOT NULL REFERENCES movies(id),
                room_id INTEGER NOT NULL REFERENCES rooms(id),
                show_date TEXT NOT NULL,
                show_time TEXT NOT NULL,
                price REAL NOT NULL
            );
            CREATE TABLE tickets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                showtime_id INTEGER NOT NULL REFERENCES showtimes(id),
                customer_name TEXT NOT NULL,
                seats TEXT NOT NULL,
                total REAL NOT NULL,
                status TEXT DEFAULT 'Đã thanh toán',
                created_at TEXT NOT NULL
            );
            CREATE TABLE audit_logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                action TEXT,
                created_at TEXT NOT NULL
            );
            INSERT INTO users VALUES(1,'admin','salt:hash','Admin');
            INSERT INTO movies VALUES(1,'Legacy film','Drama',90,'P','','Đang chiếu');
            INSERT INTO rooms VALUES(1,'Legacy room',60);
            INSERT INTO showtimes VALUES(1,1,1,'2099-01-01','10:00',75000);
            INSERT INTO tickets VALUES(1,1,'Legacy customer','A01,A02',150000,'Đã thanh toán','test');
            PRAGMA user_version=1;
        """)
        connection.close()

        original_path = database.DATABASE_PATH
        original_backup_dir = database.BACKUP_DIR
        database.DATABASE_PATH = legacy_path
        database.BACKUP_DIR = self.root / "migration-backups"
        try:
            database.initialize_database()
            migrated = database.get_connection()
            try:
                columns = {
                    row["name"] for row in
                    migrated.execute("PRAGMA table_info(tickets)")
                }
                self.assertNotIn("seats", columns)
                self.assertEqual(
                    [
                        row["seat_index"] for row in migrated.execute(
                            "SELECT seat_index FROM ticket_seats ORDER BY seat_index"
                        )
                    ],
                    [1, 2],
                )
                self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 3)
            finally:
                migrated.close()
            self.assertEqual(
                len(list((self.root / "migration-backups").glob("pre-seat-migration-*.sqlite3"))),
                1,
            )
        finally:
            database.DATABASE_PATH = original_path
            database.BACKUP_DIR = original_backup_dir


if __name__ == "__main__":
    unittest.main()
