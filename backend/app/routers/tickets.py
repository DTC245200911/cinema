from datetime import datetime
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.app.database import get_connection
from backend.app.security import current_user, log_action
from backend.app.services.cinema import seat_index

router = APIRouter()


@router.post("/api/tickets")
async def sell_ticket(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Chưa đăng nhập"},
            status_code=401,
        )
    try:
        data = await request.json()
    except ValueError:
        return JSONResponse(
            {"ok": False, "message": "Dữ liệu yêu cầu không hợp lệ."},
            status_code=400,
        )

    connection = None
    try:
        showtime_id = int(data["showtime_id"])
        customer = str(data["customer_name"]).strip()
        seats = [
            str(seat).strip().upper()
            for seat in data["seats"]
            if str(seat).strip()
        ]
        if not customer or not seats:
            raise ValueError("Thiếu tên khách hoặc ghế.")

        connection = get_connection()
        connection.execute("BEGIN IMMEDIATE")
        show = connection.execute("""
            SELECT s.price,r.capacity FROM showtimes s
            JOIN rooms r ON r.id=s.room_id WHERE s.id=?
        """, (showtime_id,)).fetchone()
        if not show:
            raise ValueError("Suất chiếu không tồn tại.")
        if len(set(seats)) != len(seats):
            raise ValueError("Danh sách có ghế bị lặp.")
        invalid = [
            seat for seat in seats
            if seat_index(seat, show["capacity"]) is None
        ]
        if invalid:
            raise ValueError("Ghế không thuộc sơ đồ phòng: " + ", ".join(invalid))

        existing = connection.execute(
            """SELECT seat_index FROM ticket_seats
               WHERE showtime_id=?""",
            (showtime_id,),
        ).fetchall()
        sold_indices = {row["seat_index"] for row in existing}
        conflict = sorted(
            seat for seat in seats
            if seat_index(seat, show["capacity"]) in sold_indices
        )
        if conflict:
            raise ValueError("Ghế đã được bán: " + ", ".join(conflict))

        total = float(show["price"]) * len(seats)
        cursor = connection.execute(
            """INSERT INTO tickets
               (showtime_id,customer_name,total,status,created_at)
               VALUES(?,?,?,?,?)""",
            (showtime_id, customer, total, "Đã thanh toán",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        connection.executemany(
            """INSERT INTO ticket_seats(ticket_id,showtime_id,seat_index)
               VALUES(?,?,?)""",
            [
                (cursor.lastrowid, showtime_id, seat_index(seat, show["capacity"]))
                for seat in seats
            ],
        )
        connection.commit()
    except (KeyError, TypeError, ValueError, sqlite3.IntegrityError) as exc:
        if connection:
            connection.rollback()
        status_code = 409 if isinstance(exc, sqlite3.IntegrityError) else 400
        message = (
            "Một hoặc nhiều ghế vừa được đặt bởi giao dịch khác."
            if status_code == 409 else str(exc)
        )
        return JSONResponse(
            {"ok": False, "message": message},
            status_code=status_code,
        )
    finally:
        if connection:
            connection.close()

    log_action(user["username"], f"Bán vé cho {customer}: {','.join(seats)}")
    return {"ok": True, "total": total}
