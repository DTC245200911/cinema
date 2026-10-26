import os
import sqlite3
import hashlib
import secrets
import urllib.request
import urllib.error
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from prompts import movie_prompt, recommend_prompt, report_prompt

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

DB = BASE_DIR / "cinema.db"

app = FastAPI(title="Cinema AI Management")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def hash_password(password):
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000)
    return salt.hex() + ":" + key.hex()

def verify_password(password, stored):
    try:
        salt, key = stored.split(":")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120000)
        return secrets.compare_digest(check.hex(), key)
    except Exception:
        return False

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS movies(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        genre TEXT NOT NULL,
        duration INTEGER NOT NULL,
        age TEXT DEFAULT 'T13',
        description TEXT DEFAULT '',
        status TEXT DEFAULT 'Đang chiếu'
    );
    CREATE TABLE IF NOT EXISTS rooms(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        capacity INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS showtimes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movie_id INTEGER NOT NULL,
        room_id INTEGER NOT NULL,
        show_date TEXT NOT NULL,
        show_time TEXT NOT NULL,
        price REAL NOT NULL,
        FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE,
        FOREIGN KEY(room_id) REFERENCES rooms(id)
    );
    CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        showtime_id INTEGER NOT NULL,
        customer_name TEXT NOT NULL,
        seats TEXT NOT NULL,
        total REAL NOT NULL,
        status TEXT DEFAULT 'Đã thanh toán',
        created_at TEXT NOT NULL,
        FOREIGN KEY(showtime_id) REFERENCES showtimes(id)
    );
    CREATE TABLE IF NOT EXISTS audit_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        action TEXT,
        created_at TEXT NOT NULL
    );
    """)

    if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        for u, r in [("admin","Admin"),("manager","Quản lý"),("cashier","Thu ngân")]:
            con.execute("INSERT INTO users(username,password,role) VALUES(?,?,?)",
                        (u, hash_password("123456"), r))

    if con.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 0:
        movies = [
            ("Dune: Part Two", "Khoa học viễn tưởng", 166, "T13",
             "Paul Atreides đồng hành cùng Chani và người Fremen trên hành trình báo thù.", "Đang chiếu"),
            ("Inside Out 2", "Hoạt hình", 96, "P",
             "Riley bước vào tuổi thiếu niên và đối diện với những cảm xúc mới.", "Đang chiếu"),
            ("The Batman", "Hành động", 176, "T16",
             "Batman điều tra một chuỗi vụ án bí ẩn tại Gotham.", "Sắp chiếu"),
            ("Avatar: The Way of Water", "Phiêu lưu", 192, "T13",
             "Gia đình Sully tìm nơi trú ẩn mới và bảo vệ đại dương Pandora.", "Đang chiếu")
        ]
        con.executemany("""INSERT INTO movies
            (title,genre,duration,age,description,status) VALUES(?,?,?,?,?,?)""", movies)

    if con.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0:
        con.executemany("INSERT INTO rooms(name,capacity) VALUES(?,?)",
                        [("Phòng 01", 60), ("Phòng 02", 80), ("Phòng VIP", 40)])

    if con.execute("SELECT COUNT(*) FROM showtimes").fetchone()[0] == 0:
        mids = [r["id"] for r in con.execute("SELECT id FROM movies ORDER BY id")]
        rooms = [r["id"] for r in con.execute("SELECT id FROM rooms ORDER BY id")]
        today = datetime.now().strftime("%Y-%m-%d")
        rows = []
        for i, mid in enumerate(mids):
            rows.append((mid, rooms[i % len(rooms)], today, ["10:00","13:30","16:30","19:30"][i], 75000 + i*5000))
        con.executemany("""INSERT INTO showtimes
            (movie_id,room_id,show_date,show_time,price) VALUES(?,?,?,?,?)""", rows)

    if con.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0:
        sid = con.execute("SELECT id FROM showtimes ORDER BY id LIMIT 1").fetchone()["id"]
        con.execute("""INSERT INTO tickets
            (showtime_id,customer_name,seats,total,status,created_at)
            VALUES(?,?,?,?,?,?)""",
            (sid, "Nguyễn Văn A", "A01,A02", 150000, "Đã thanh toán",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    con.close()

init_db()

def current_user(request):
    username = request.cookies.get("cinema_user")
    if not username:
        return None
    con = db()
    row = con.execute("SELECT id,username,role FROM users WHERE username=?", (username,)).fetchone()
    con.close()
    return dict(row) if row else None

def log_action(username, action):
    con = db()
    con.execute("INSERT INTO audit_logs(username,action,created_at) VALUES(?,?,?)",
                (username, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    con.close()

def movie_rows(q=""):
    con = db()
    if q:
        rows = con.execute("""SELECT * FROM movies
            WHERE title LIKE ? OR genre LIKE ? ORDER BY id DESC""",
            (f"%{q}%", f"%{q}%")).fetchall()
    else:
        rows = con.execute("SELECT * FROM movies ORDER BY id DESC").fetchall()
    con.close()
    return [dict(x) for x in rows]

def dashboard():
    con = db()
    movie_count = con.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    ticket_count = con.execute("SELECT COUNT(*) FROM tickets WHERE status='Đã thanh toán'").fetchone()[0]
    revenue = con.execute("SELECT COALESCE(SUM(total),0) FROM tickets WHERE status='Đã thanh toán'").fetchone()[0]
    show_count = con.execute("SELECT COUNT(*) FROM showtimes").fetchone()[0]
    revenue_rows = con.execute("""
        SELECT m.title, COALESCE(SUM(t.total),0) revenue
        FROM movies m
        LEFT JOIN showtimes s ON s.movie_id=m.id
        LEFT JOIN tickets t ON t.showtime_id=s.id AND t.status='Đã thanh toán'
        GROUP BY m.id ORDER BY revenue DESC
    """).fetchall()
    con.close()
    return {
        "movie_count": movie_count,
        "ticket_count": ticket_count,
        "revenue": revenue,
        "show_count": show_count,
        "chart_labels": [r["title"] for r in revenue_rows],
        "chart_values": [r["revenue"] for r in revenue_rows]
    }

@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = ""):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "user": user,
            "movies": movie_rows(q),
            "q": q,
            "dashboard": dashboard()
        }
    )

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
  return templates.TemplateResponse(
    request=request,
    name="login.html",
    context={"request": request, "error": ""}
)

@app.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    con = db()
    row = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    con.close()
    if not row or not verify_password(password, row["password"]):
        return templates.TemplateResponse(
    request=request,
    name="login.html",
    context={
        "request": request,
        "error": "Sai tài khoản hoặc mật khẩu."
    }
)
    log_action(username, "Đăng nhập hệ thống")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("cinema_user", username, httponly=True, samesite="lax")
    return response

@app.get("/logout")
def logout(request: Request):
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("cinema_user")
    return response

@app.post("/movies")
def create_movie(request: Request, title: str = Form(...), genre: str = Form(...),
                 duration: int = Form(...), age: str = Form("T13"),
                 description: str = Form(""), status: str = Form("Đang chiếu")):
    user = current_user(request)
    if not user or user["role"] not in ["Admin", "Quản lý"]:
        return JSONResponse({"ok": False, "message": "Bạn không có quyền thêm phim."}, status_code=403)
    if duration <= 0:
        return JSONResponse({"ok": False, "message": "Thời lượng phải lớn hơn 0."}, status_code=400)
    con = db()
    con.execute("""INSERT INTO movies(title,genre,duration,age,description,status)
                   VALUES(?,?,?,?,?,?)""", (title,genre,duration,age,description,status))
    con.commit()
    con.close()
    log_action(user["username"], f"Thêm phim: {title}")
    return RedirectResponse("/", status_code=303)

@app.post("/movies/{movie_id}/delete")
def delete_movie(request: Request, movie_id: int):
    user = current_user(request)
    if not user or user["role"] not in ["Admin", "Quản lý"]:
        return JSONResponse({"ok": False, "message": "Không có quyền xóa."}, status_code=403)
    con = db()
    row = con.execute("SELECT title FROM movies WHERE id=?", (movie_id,)).fetchone()
    if row:
        con.execute("DELETE FROM movies WHERE id=?", (movie_id,))
        con.commit()
        log_action(user["username"], f"Xóa phim: {row['title']}")
    con.close()
    return RedirectResponse("/", status_code=303)

@app.get("/api/showtimes")
def showtimes_api(request: Request):
    if not current_user(request):
        return JSONResponse({"error":"Chưa đăng nhập"}, status_code=401)
    con = db()
    rows = con.execute("""
        SELECT s.id,m.title,r.name room,s.show_date,s.show_time,s.price
        FROM showtimes s JOIN movies m ON m.id=s.movie_id JOIN rooms r ON r.id=s.room_id
        ORDER BY s.show_date,s.show_time
    """).fetchall()
    con.close()
    return [dict(x) for x in rows]

@app.post("/api/tickets")
async def sell_ticket(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"ok":False,"message":"Chưa đăng nhập"}, status_code=401)
    data = await request.json()
    try:
        showtime_id = int(data["showtime_id"])
        customer = str(data["customer_name"]).strip()
        seats = [str(x).strip().upper() for x in data["seats"] if str(x).strip()]
        if not customer or not seats:
            raise ValueError("Thiếu tên khách hoặc ghế.")
        con = db()
        show = con.execute("SELECT price FROM showtimes WHERE id=?", (showtime_id,)).fetchone()
        if not show:
            raise ValueError("Suất chiếu không tồn tại.")
        existing = con.execute(
            "SELECT seats FROM tickets WHERE showtime_id=? AND status='Đã thanh toán'", (showtime_id,)
        ).fetchall()
        sold = set()
        for x in existing:
            sold.update(x["seats"].split(","))
        conflict = sorted(set(seats) & sold)
        if conflict:
            raise ValueError("Ghế đã được bán: " + ", ".join(conflict))
        total = float(show["price"]) * len(seats)
        con.execute("""INSERT INTO tickets
            (showtime_id,customer_name,seats,total,status,created_at)
            VALUES(?,?,?,?,?,?)""",
            (showtime_id, customer, ",".join(seats), total, "Đã thanh toán",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
        con.close()
        log_action(user["username"], f"Bán vé cho {customer}: {','.join(seats)}")
        return {"ok":True, "total":total}
    except Exception as e:
        return JSONResponse({"ok":False,"message":str(e)}, status_code=400)

def gemini(prompt):
    if not GEMINI_API_KEY:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ]
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode())
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None


def build_demo_ai_answer(message: str, movies: list, dashboard_data: dict | None = None) -> str:
    text = message.lower()
    active_movies = [m for m in movies if m.get("status") == "Đang chiếu"]
    genres = sorted({m.get("genre", "") for m in movies if m.get("genre")})
    genre_hint = ", ".join(genres[:3]) if genres else "chưa có dữ liệu"

    if "gợi ý" in text or "phim" in text:
        if active_movies:
            sample = ", ".join(m["title"] for m in active_movies[:3])
            return (
                f"Các phim đang chiếu hiện có: {sample}. "
                f"Nếu bạn thích thể loại cụ thể, tôi có thể gợi ý theo loại như {genre_hint}."
            )
        return "Hiện chưa có phim đang chiếu trong dữ liệu hệ thống."

    if "doanh thu" in text or "tổng" in text or "revenue" in text:
        if dashboard_data is None:
            dashboard_data = dashboard()
        return (
            f"Doanh thu hiện tại là {dashboard_data['revenue']:,.0f} VNĐ, "
            f"với {dashboard_data['ticket_count']} vé đã thanh toán, "
            f"{dashboard_data['movie_count']} phim và {dashboard_data['show_count']} suất chiếu."
        )

    if "thể loại" in text or "genre" in text:
        if genres:
            return f"Các thể loại hiện có trong hệ thống: {', '.join(genres)}."
        return "Dữ liệu thể loại chưa được cung cấp."

    if "suất" in text or "lịch" in text:
        if active_movies:
            sample = ", ".join(m["title"] for m in active_movies[:3])
            return (
                "Bạn có thể xem lịch chiếu theo từng phim trong mục Suất chiếu; "
                f"dữ liệu hiện tại đang bao gồm các phim đang chiếu: {sample}."
            )
        return "Dữ liệu suất chiếu chưa được cung cấp."

    if "hiện có" in text or "có gì" in text or "mở" in text:
        if movies:
            return "Hiện hệ thống có các phim: " + ", ".join(m["title"] for m in movies[:3]) + "."
        return "Hiện chưa có dữ liệu phim trong hệ thống."

    return (
        "Tôi đang hỗ trợ quản lý rạp chiếu phim. Hãy hỏi về phim đang chiếu, thể loại, "
        "suất chiếu hoặc doanh thu."
    )


@app.post("/api/ai")
async def ai_chat(request: Request):
    user = current_user(request)

    if not user:
        return JSONResponse(
            {"error": "Chưa đăng nhập"},
            status_code=401
        )

    data = await request.json()
    message = str(data.get("message", "")).strip()

    if not message:
        return JSONResponse(
            {"error": "Hãy nhập câu hỏi."},
            status_code=400
        )

    con = db()

    movies = [
        dict(x)
        for x in con.execute(
            "SELECT title,genre,duration,age,status,description FROM movies"
        ).fetchall()
    ]

    con.close()

    context = json.dumps(movies, ensure_ascii=False)

    prompt = movie_prompt(
        context,
        message
    )

    answer = gemini(prompt)

    if not answer:
        answer = build_demo_ai_answer(message, movies, dashboard())

    return {"answer": answer}


@app.get("/api/ai/recommend")
def ai_recommend(request: Request):
    user = current_user(request)

    if not user:
        return JSONResponse(
            {"error": "Chưa đăng nhập"},
            status_code=401
        )

    con = db()

    rows = con.execute("""
        SELECT title,genre,duration,age,description
        FROM movies
        WHERE status='Đang chiếu'
        LIMIT 4
    """).fetchall()

    con.close()

    data = [dict(x) for x in rows]

    prompt = recommend_prompt(
        json.dumps(data, ensure_ascii=False)
    )

    answer = gemini(prompt)

    if not answer:
        answer = build_demo_ai_answer("gợi ý phim", data)

    return {"answer": answer}


@app.get("/api/ai/report")
def ai_report(request: Request):
    user = current_user(request)

    if not user or user["role"] not in ["Admin", "Quản lý"]:
        return JSONResponse(
            {"error": "Chỉ Quản lý/Admin được xem báo cáo AI."},
            status_code=403
        )

    d = dashboard()

    prompt = report_prompt(
        d["revenue"],
        d["ticket_count"],
        d["movie_count"],
        d["show_count"]
    )

    answer = gemini(prompt) or (
        f"Trong dữ liệu mẫu, rạp có "
        f"{d['ticket_count']} vé đã thanh toán, "
        f"{d['movie_count']} phim và "
        f"{d['show_count']} suất chiếu. "
        f"Tổng doanh thu là {d['revenue']:,.0f} VNĐ."
    )

    return {"answer": answer}