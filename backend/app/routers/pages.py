from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.config import (
    GEMINI_SHARE_CINEMA_DATA,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
    SESSION_TTL_SECONDS,
    TEMPLATES_DIR,
)
from backend.app.database import dashboard_data, get_connection, list_movies, list_rooms
from backend.app.security import (
    create_session,
    current_user,
    log_action,
    revoke_session,
    verify_password,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = ""):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    can_view_reports = user["role"] in {"Admin", "Quản lý"}
    dashboard = dashboard_data() if can_view_reports else {
        "movie_count": 0,
        "ticket_count": 0,
        "revenue": 0,
        "show_count": 0,
        "chart_labels": [],
        "chart_values": [],
    }
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "user": user,
            "movies": list_movies(q),
            "all_movies": list_movies(),
            "rooms": list_rooms(),
            "q": q,
            "dashboard": dashboard,
            "can_view_reports": can_view_reports,
            "gemini_share_cinema_data": GEMINI_SHARE_CINEMA_DATA,
        },
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    connection = get_connection()
    try:
        active_users = connection.execute("""
            SELECT COUNT(*) FROM users u
            WHERE NOT EXISTS (
                SELECT 1 FROM credential_bootstrap b WHERE b.user_id=u.id
            )
        """).fetchone()[0]
    finally:
        connection.close()
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "error": "",
            "setup_required": active_users == 0,
        },
    )


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
    finally:
        connection.close()

    if not row or not verify_password(password, row["password"]):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "request": request,
                "error": "Sai tài khoản hoặc mật khẩu.",
                "setup_required": False,
            },
        )
    log_action(username, "Đăng nhập hệ thống")
    response = RedirectResponse("/", status_code=303)
    session_token = create_session(row["id"])
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.delete_cookie("cinema_user", path="/")
    return response


@router.get("/logout")
def logout(request: Request):
    revoke_session(request.cookies.get(SESSION_COOKIE_NAME))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie("cinema_user", path="/")
    return response
