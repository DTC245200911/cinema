import math
from datetime import date, datetime


def seat_name(index, capacity):
    columns = max(1, (capacity + 4) // 5)
    row, column = divmod(index - 1, columns)
    return f"{chr(65 + row)}{column + 1:02d}"


def seat_index(name, capacity):
    if len(name) < 2 or not name[0].isalpha() or not name[1:].isdigit():
        return None
    row = ord(name[0]) - 65
    column = int(name[1:]) - 1
    columns = max(1, (capacity + 4) // 5)
    index = row * columns + column + 1
    if not (0 <= row < 5 and 0 <= column < columns and index <= capacity):
        return None
    return index if seat_name(index, capacity) == name else None


def validate_showtime(
    connection,
    movie_id,
    room_id,
    show_date,
    show_time,
    price,
    exclude_id=None,
):
    movie = connection.execute(
        "SELECT duration FROM movies WHERE id=?", (movie_id,)
    ).fetchone()
    room = connection.execute(
        "SELECT id FROM rooms WHERE id=?", (room_id,)
    ).fetchone()
    if not movie or not room:
        raise ValueError("Phim hoặc phòng chiếu không tồn tại.")
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Giá vé phải lớn hơn 0.")
    if not float(price).is_integer() or int(price) % 1000:
        raise ValueError("Giá vé phải là bội số của 1.000 VNĐ.")
    try:
        date.fromisoformat(show_date)
        start = datetime.strptime(show_time, "%H:%M")
    except ValueError as exc:
        raise ValueError("Ngày hoặc giờ chiếu không hợp lệ.") from exc

    start_minutes = start.hour * 60 + start.minute
    end_minutes = start_minutes + movie["duration"]
    query = """
        SELECT s.id, s.show_time, m.duration
        FROM showtimes s JOIN movies m ON m.id=s.movie_id
        WHERE s.room_id=? AND s.show_date=?
    """
    params = [room_id, show_date]
    if exclude_id is not None:
        query += " AND s.id<>?"
        params.append(exclude_id)
    for existing in connection.execute(query, params):
        existing_start = datetime.strptime(existing["show_time"], "%H:%M")
        existing_start_minutes = existing_start.hour * 60 + existing_start.minute
        existing_end_minutes = existing_start_minutes + existing["duration"]
        if start_minutes < existing_end_minutes and existing_start_minutes < end_minutes:
            raise ValueError(
                "Suất chiếu bị trùng thời gian với suất khác trong phòng."
            )
