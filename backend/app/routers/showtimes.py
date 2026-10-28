import sqlite3

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from backend.app.database import (
    get_connection,
    list_rooms_with_show_count,
    list_showtimes,
    showtime_seat_status,
)
from backend.app.security import current_user, log_action, management_user
from backend.app.services.cinema import seat_index, validate_showtime

router = APIRouter()


@router.get("/api/showtimes")
def showtimes_api(request: Request):
    if not current_user(request):
        return JSONResponse({"error": "Chưa đăng nhập"}, status_code=401)
    return list_showtimes()


@router.get("/api/rooms")
def rooms_api(request: Request):
    if not current_user(request):
        return JSONResponse({"error": "Chưa đăng nhập"}, status_code=401)
    return list_rooms_with_show_count()


@router.get("/api/showtimes/{showtime_id}/seats")
def showtime_seats_api(request: Request, showtime_id: int):
    if not current_user(request):
        return JSONResponse({"error": "Chưa đăng nhập"}, status_code=401)
    status = showtime_seat_status(showtime_id)
    if not status:
        return JSONResponse({"error": "Suất chiếu không tồn tại."}, status_code=404)
    return status


@router.post("/showtimes")
def create_showtime(
    request: Request,
    movie_id: int = Form(...),
    room_id: int = Form(...),
    show_date: str = Form(...),
    show_time: str = Form(...),
    price: float = Form(...),
):
    user = management_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Bạn không có quyền quản lý suất chiếu."},
            status_code=403,
        )
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            validate_showtime(
                connection, movie_id, room_id, show_date, show_time, price
            )
        except ValueError as exc:
            connection.rollback()
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        connection.execute(
            """INSERT INTO showtimes(movie_id,room_id,show_date,show_time,price)
               VALUES(?,?,?,?,?)""",
            (movie_id, room_id, show_date, show_time, price),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        return JSONResponse(
            {"ok": False, "message": f"Dữ liệu suất chiếu không hợp lệ: {exc}"},
            status_code=409,
        )
    finally:
        connection.close()
    log_action(
        user["username"],
        f"Tạo suất chiếu phim #{movie_id} tại phòng #{room_id}",
    )
    return {"ok": True}


@router.post("/showtimes/{showtime_id}")
def update_showtime(
    request: Request,
    showtime_id: int,
    movie_id: int = Form(...),
    room_id: int = Form(...),
    show_date: str = Form(...),
    show_time: str = Form(...),
    price: float = Form(...),
):
    user = management_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Bạn không có quyền quản lý suất chiếu."},
            status_code=403,
        )
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id FROM showtimes WHERE id=?", (showtime_id,)
        ).fetchone()
        if not existing:
            return JSONResponse(
                {"ok": False, "message": "Không tìm thấy suất chiếu."},
                status_code=404,
            )
        try:
            validate_showtime(
                connection, movie_id, room_id, show_date, show_time,
                price, showtime_id,
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

        target_room = connection.execute(
            "SELECT capacity FROM rooms WHERE id=?", (room_id,)
        ).fetchone()
        sold_rows = connection.execute("""
            SELECT seat_index FROM ticket_seats
            WHERE showtime_id=?
        """, (showtime_id,)).fetchall()
        invalid_seats = [
            row["seat_index"] for row in sold_rows
            if row["seat_index"] > target_room["capacity"]
        ]
        if invalid_seats:
            connection.rollback()
            return JSONResponse(
                {
                    "ok": False,
                    "message": "Phòng mới không có đủ ghế đã bán: "
                    + ", ".join(map(str, sorted(set(invalid_seats)))),
                },
                status_code=409,
            )
        connection.execute(
            """UPDATE showtimes
               SET movie_id=?,room_id=?,show_date=?,show_time=?,price=?
               WHERE id=?""",
            (movie_id, room_id, show_date, show_time, price, showtime_id),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        return JSONResponse(
            {"ok": False, "message": f"Dữ liệu suất chiếu không hợp lệ: {exc}"},
            status_code=409,
        )
    finally:
        connection.close()
    log_action(user["username"], f"Sửa suất chiếu #{showtime_id}")
    return {"ok": True}


@router.post("/showtimes/{showtime_id}/delete")
def delete_showtime(request: Request, showtime_id: int):
    user = management_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Bạn không có quyền quản lý suất chiếu."},
            status_code=403,
        )
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        showtime = connection.execute(
            "SELECT id FROM showtimes WHERE id=?", (showtime_id,)
        ).fetchone()
        if not showtime:
            connection.rollback()
            return JSONResponse(
                {"ok": False, "message": "Không tìm thấy suất chiếu."},
                status_code=404,
            )
        has_tickets = connection.execute(
            "SELECT 1 FROM tickets WHERE showtime_id=? LIMIT 1",
            (showtime_id,),
        ).fetchone()
        if has_tickets:
            connection.rollback()
            return JSONResponse(
                {"ok": False, "message": "Không thể xóa suất chiếu đã có vé."},
                status_code=409,
            )
        connection.execute("DELETE FROM showtimes WHERE id=?", (showtime_id,))
        connection.commit()
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        return JSONResponse(
            {"ok": False, "message": f"Không thể xóa suất chiếu: {exc}"},
            status_code=409,
        )
    finally:
        connection.close()
    log_action(user["username"], f"Xóa suất chiếu #{showtime_id}")
    return {"ok": True}
