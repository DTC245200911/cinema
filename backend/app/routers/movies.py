import sqlite3

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from backend.app.database import get_connection
from backend.app.security import log_action, management_user
from backend.app.services.cinema import validate_showtime

router = APIRouter()


@router.post("/movies")
def create_movie(
    request: Request,
    title: str = Form(...),
    genre: str = Form(...),
    duration: int = Form(...),
    age: str = Form("T13"),
    description: str = Form(""),
    status: str = Form("Đang chiếu"),
):
    user = management_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Bạn không có quyền thêm phim."},
            status_code=403,
        )
    title, genre = title.strip(), genre.strip()
    if not title or not genre or duration <= 0:
        return JSONResponse(
            {
                "ok": False,
                "message": "Tên, thể loại phim không được để trống và thời lượng phải lớn hơn 0.",
            },
            status_code=400,
        )

    connection = get_connection()
    try:
        connection.execute(
            """INSERT INTO movies(title,genre,duration,age,description,status)
               VALUES(?,?,?,?,?,?)""",
            (title, genre, duration, age.strip(), description.strip(), status),
        )
        connection.commit()
    finally:
        connection.close()
    log_action(user["username"], f"Thêm phim: {title}")
    return RedirectResponse("/", status_code=303)


@router.post("/movies/{movie_id}")
def update_movie(
    request: Request,
    movie_id: int,
    title: str = Form(...),
    genre: str = Form(...),
    duration: int = Form(...),
    age: str = Form("T13"),
    description: str = Form(""),
    status: str = Form("Đang chiếu"),
):
    user = management_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Bạn không có quyền sửa phim."},
            status_code=403,
        )
    title, genre = title.strip(), genre.strip()
    if not title or not genre or duration <= 0:
        return JSONResponse(
            {
                "ok": False,
                "message": "Tên, thể loại phim không được để trống và thời lượng phải lớn hơn 0.",
            },
            status_code=400,
        )

    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        movie = connection.execute(
            "SELECT id FROM movies WHERE id=?", (movie_id,)
        ).fetchone()
        if not movie:
            return JSONResponse(
                {"ok": False, "message": "Không tìm thấy phim."},
                status_code=404,
            )
        try:
            connection.execute(
                """UPDATE movies
                   SET title=?,genre=?,duration=?,age=?,description=?,status=?
                   WHERE id=?""",
                (title, genre, duration, age.strip(), description.strip(), status, movie_id),
            )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            return JSONResponse(
                {"ok": False, "message": f"Không thể cập nhật lịch chiếu: {exc}"},
                status_code=409,
            )
        schedules = connection.execute(
            """SELECT id,room_id,show_date,show_time,price
               FROM showtimes WHERE movie_id=?""",
            (movie_id,),
        ).fetchall()
        try:
            for schedule in schedules:
                validate_showtime(
                    connection,
                    movie_id,
                    schedule["room_id"],
                    schedule["show_date"],
                    schedule["show_time"],
                    schedule["price"],
                    schedule["id"],
                )
        except ValueError as exc:
            connection.rollback()
            return JSONResponse(
                {"ok": False, "message": str(exc)},
                status_code=409,
            )
        connection.commit()
    finally:
        connection.close()
    log_action(user["username"], f"Sửa phim: {title}")
    return RedirectResponse("/", status_code=303)


@router.post("/movies/{movie_id}/delete")
def delete_movie(request: Request, movie_id: int):
    user = management_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Không có quyền xóa."},
            status_code=403,
        )
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        movie = connection.execute(
            "SELECT title FROM movies WHERE id=?", (movie_id,)
        ).fetchone()
        if not movie:
            connection.rollback()
            return RedirectResponse("/", status_code=303)
        ticket = connection.execute("""
            SELECT 1 FROM tickets t
            JOIN showtimes s ON s.id=t.showtime_id
            WHERE s.movie_id=? LIMIT 1
        """, (movie_id,)).fetchone()
        if ticket:
            connection.rollback()
            return JSONResponse(
                {"ok": False, "message": "Không thể xóa phim đã có vé bán."},
                status_code=409,
            )
        connection.execute("DELETE FROM movies WHERE id=?", (movie_id,))
        connection.commit()
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        return JSONResponse(
            {"ok": False, "message": f"Không thể xóa phim: {exc}"},
            status_code=409,
        )
    finally:
        connection.close()
    log_action(user["username"], f"Xóa phim: {movie['title']}")
    return RedirectResponse("/", status_code=303)
