import logging
import json
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from backend.app.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_SHARE_CINEMA_DATA,
)
from backend.app.database import get_connection
from backend.app.prompts import SYSTEM_PROMPT, movie_prompt
from backend.app.services.cinema import seat_index, seat_name

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeminiResult:
    answer: Optional[str]
    error: Optional[str]


def _normalize(text):
    normalized = unicodedata.normalize("NFD", text.lower())
    normalized = "".join(
        character for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    normalized = normalized.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _cinema_data():
    connection = get_connection()
    try:
        connection.execute("BEGIN")
        movies = [
            dict(row) for row in connection.execute("""
                SELECT id,title,genre,duration,age,status,description
                FROM movies ORDER BY title
            """).fetchall()
        ]
        showtimes = [
            dict(row) for row in connection.execute("""
                SELECT s.id,s.movie_id,s.room_id,m.title,m.genre,r.name room,
                       r.capacity,m.duration,s.show_date,s.show_time,s.price
                FROM showtimes s
                JOIN movies m ON m.id=s.movie_id
                JOIN rooms r ON r.id=s.room_id
                ORDER BY s.show_date,s.show_time
            """).fetchall()
        ]
        sold_rows = connection.execute("""
            SELECT ts.showtime_id,ts.seat_index,r.capacity
            FROM ticket_seats ts
            JOIN showtimes s ON s.id=ts.showtime_id
            JOIN rooms r ON r.id=s.room_id
            ORDER BY ts.showtime_id,ts.seat_index
        """).fetchall()
        report = {
            "movie_count": connection.execute(
                "SELECT COUNT(*) FROM movies"
            ).fetchone()[0],
            "ticket_count": connection.execute(
                "SELECT COUNT(*) FROM tickets WHERE status='Đã thanh toán'"
            ).fetchone()[0],
            "revenue": connection.execute(
                """SELECT COALESCE(SUM(total),0) FROM tickets
                   WHERE status='Đã thanh toán'"""
            ).fetchone()[0],
            "show_count": connection.execute(
                "SELECT COUNT(*) FROM showtimes"
            ).fetchone()[0],
        }
    finally:
        connection.close()
    sold_by_showtime = {}
    for row in sold_rows:
        sold_by_showtime.setdefault(row["showtime_id"], []).append(
            seat_name(row["seat_index"], row["capacity"])
        )
    for showtime in showtimes:
        sold = sold_by_showtime.get(showtime["id"], [])
        showtime["sold_seats"] = sold
        showtime["available_seats"] = showtime["capacity"] - len(sold)
    return movies, showtimes, report


def _genre_matches(message, movies):
    normalized_message = f" {_normalize(message)} "
    genres = sorted(
        {movie["genre"] for movie in movies if movie.get("genre")},
        key=lambda genre: len(_normalize(genre)),
        reverse=True,
    )
    exact_match = next(
        (
            genre for genre in genres
            if f" {_normalize(genre)} " in normalized_message
        ),
        None,
    )
    if exact_match:
        return exact_match

    partial_matches = []
    for genre in genres:
        genre_words = _normalize(genre).split()
        for word_count in range(len(genre_words) - 1, 1, -1):
            prefix = " ".join(genre_words[:word_count])
            if f" {prefix} " in normalized_message:
                partial_matches.append((word_count, genre))
                break
    if not partial_matches:
        return None

    longest_match = max(word_count for word_count, _genre in partial_matches)
    matched_genres = {
        genre for word_count, genre in partial_matches
        if word_count == longest_match
    }
    return next(iter(matched_genres)) if len(matched_genres) == 1 else None


def _movie_matches(message, movies):
    normalized_message = f" {_normalize(message)} "
    matching = [
        movie for movie in movies
        if f" {_normalize(movie['title'])} " in normalized_message
    ]
    return max(matching, key=lambda movie: len(movie["title"]), default=None)


def _movie_line(movie, showtimes):
    line = (
        f"- {movie['title']} — {movie['status']}; {movie['genre']}, "
        f"{movie['duration']} phút, phân loại {movie['age']}."
    )
    if movie.get("description"):
        line += f" {movie['description']}"
    movie_shows = [
        showtime for showtime in showtimes
        if showtime["movie_id"] == movie["id"]
        and showtime["show_date"] >= date.today().isoformat()
    ]
    if movie_shows:
        schedules = "; ".join(
            f"{showtime['show_date']} {showtime['show_time']} tại "
            f"{showtime['room']} (vé {showtime['price']:,.0f} VNĐ)"
            for showtime in movie_shows
        )
        line += f" Suất chiếu: {schedules}."
    else:
        line += " Hiện chưa có suất chiếu được lên lịch."
    return line


def _seat_answer(message, movies, showtimes):
    normalized_message = _normalize(message)
    if not any(term in normalized_message for term in (
        "ghe", "cho ngoi", "con cho", "con trong",
    )):
        return None

    movie = _movie_matches(message, movies)
    genre = _genre_matches(message, movies) if movie is None else None
    genres_by_movie_id = {
        item["id"]: item.get("genre", "") for item in movies
    }
    requested_room = next(
        (
            showtime["room"] for showtime in showtimes
            if f" {_normalize(showtime['room'])} " in f" {normalized_message} "
        ),
        None,
    )
    matching_shows = [
        showtime for showtime in showtimes
        if showtime["show_date"] >= date.today().isoformat()
        and (movie is None or showtime["movie_id"] == movie["id"])
        and (
            genre is None
            or _normalize(genres_by_movie_id.get(showtime["movie_id"], ""))
            == _normalize(genre)
        )
        and (requested_room is None or showtime["room"] == requested_room)
    ]
    if not matching_shows:
        return "Không tìm thấy suất chiếu sắp tới phù hợp để tra cứu ghế."

    requested_seat = re.search(r"\b([A-E]\d{2})\b", message, re.IGNORECASE)
    lines = []
    for showtime in matching_shows:
        sold = showtime["sold_seats"]
        if requested_seat:
            seat = requested_seat.group(1).upper()
            if seat_index(seat, showtime["capacity"]) is None:
                status = f"{seat} không thuộc sơ đồ ghế của {showtime['room']}."
            elif seat in sold:
                status = f"{seat} đã được đặt."
            else:
                status = f"{seat} hiện còn trống."
            lines.append(
                f"- {showtime['title']} — {showtime['show_date']} "
                f"{showtime['show_time']}, {showtime['room']}: {status}"
            )
        else:
            lines.append(
                f"- {showtime['title']} — {showtime['show_date']} "
                f"{showtime['show_time']}, {showtime['room']}: còn "
                f"{showtime['available_seats']}/{showtime['capacity']} ghế."
            )
    return "Trạng thái ghế theo suất chiếu:\n" + "\n".join(lines)


def _database_answer(message, movies, showtimes, report):
    normalized_message = _normalize(message)
    if is_report_question(message):
        return (
            f"Doanh thu từ vé đã thanh toán là {report['revenue']:,.0f} VNĐ. "
            f"Đã bán {report['ticket_count']} vé; hệ thống có "
            f"{report['movie_count']} phim và {report['show_count']} suất chiếu."
        )

    seat_answer = _seat_answer(message, movies, showtimes)
    if seat_answer:
        return seat_answer

    genre = _genre_matches(message, movies)
    if genre:
        requested_status = None
        if "dang chieu" in normalized_message:
            requested_status = "Đang chiếu"
        elif "sap chieu" in normalized_message:
            requested_status = "Sắp chiếu"
        genre_movies = [
            movie for movie in movies
            if _normalize(movie.get("genre", "")) == _normalize(genre)
            and (
                requested_status is None
                or movie.get("status") == requested_status
            )
        ]
        if not genre_movies:
            if requested_status:
                return (
                    f"Hiện chưa có phim thể loại {genre} ở trạng thái "
                    f"{requested_status}."
                )
            return f"Hiện chưa có phim thuộc thể loại {genre} trong dữ liệu."
        return (
            f"Phim thuộc thể loại {genre} hiện có:\n"
            + "\n".join(_movie_line(movie, showtimes) for movie in genre_movies)
        )

    if any(term in normalized_message for term in (
        "the loai", "genre", "goi y the loai", "co the loai nao",
    )):
        genres = sorted(
            {movie["genre"] for movie in movies if movie.get("genre")},
            key=_normalize,
        )
        if not genres:
            return "Hiện chưa có dữ liệu thể loại phim."
        genre_lines = []
        for item in genres:
            titles = [
                movie["title"] for movie in movies
                if _normalize(movie.get("genre", "")) == _normalize(item)
            ]
            genre_lines.append(f"- {item}: {', '.join(titles)}")
        return "Các thể loại đang có trong dữ liệu:\n" + "\n".join(genre_lines)

    movie = _movie_matches(message, movies)
    if movie:
        return _movie_line(movie, showtimes)

    if any(term in normalized_message for term in (
        "suat chieu", "lich chieu", "gio chieu", "gia ve", "phong chieu",
    )):
        genre_filter = _genre_matches(message, movies)
        matching_shows = showtimes
        if genre_filter:
            matching_titles = {
                movie["title"] for movie in movies
                if _normalize(movie.get("genre", "")) == _normalize(genre_filter)
            }
            matching_shows = [
                showtime for showtime in showtimes
                if showtime["title"] in matching_titles
            ]
        requested_date = None
        if "hom nay" in normalized_message:
            requested_date = date.today().isoformat()
        elif "ngay mai" in normalized_message:
            requested_date = (date.today() + timedelta(days=1)).isoformat()
        if requested_date:
            matching_shows = [
                showtime for showtime in matching_shows
                if showtime["show_date"] == requested_date
            ]
        else:
            matching_shows = [
                showtime for showtime in matching_shows
                if showtime["show_date"] >= date.today().isoformat()
            ]
        if not matching_shows:
            return "Hiện chưa có suất chiếu phù hợp trong dữ liệu."
        return "Lịch chiếu hiện có:\n" + "\n".join(
            f"- {showtime['title']} — {showtime['show_date']} "
            f"{showtime['show_time']}, {showtime['room']}, "
            f"{showtime['price']:,.0f} VNĐ/vé."
            for showtime in matching_shows
        )

    requested_status = None
    if "dang chieu" in normalized_message:
        requested_status = "Đang chiếu"
    elif "sap chieu" in normalized_message:
        requested_status = "Sắp chiếu"
    if requested_status or any(term in normalized_message for term in (
        "phim nao", "phim gi", "dang chieu", "phim hien co", "liet ke phim",
        "goi y phim", "cac phim",
    )):
        matching_movies = [
            movie for movie in movies
            if movie.get("status") == (requested_status or "Đang chiếu")
        ]
        if not matching_movies:
            return f"Hiện không có phim nào ở trạng thái {requested_status or 'Đang chiếu'}."
        return f"Phim {requested_status or 'Đang chiếu'}:\n" + "\n".join(
            _movie_line(movie, showtimes) for movie in matching_movies
        )
    return None


def is_report_question(message):
    normalized_message = _normalize(message)
    return any(term in normalized_message for term in (
        "doanh thu",
        "bao cao",
        "ve da ban",
        "tong tien",
        "tong doanh thu",
        "so ve",
        "doanh so",
    ))


def _result(answer, source, notice=None):
    result = {"answer": answer, "source": source}
    if notice:
        result["notice"] = notice
    return result


def generate_with_gemini(prompt):
    if not GEMINI_API_KEY:
        return GeminiResult(None, "Chưa cấu hình GEMINI_API_KEY.")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            result = json.loads(response.read().decode())
            answer = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            if not answer:
                raise ValueError("AI trả về nội dung rỗng.")
            return GeminiResult(answer, None)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            reason = "Gemini từ chối API key hoặc quyền truy cập."
        elif error.code == 429:
            reason = "Gemini đang giới hạn lượt gọi; vui lòng thử lại sau."
        elif error.code >= 500:
            reason = "Dịch vụ Gemini hiện đang gặp sự cố."
        else:
            reason = f"Gemini từ chối yêu cầu (HTTP {error.code})."
        LOGGER.warning("Gemini request failed: HTTP %s", error.code)
        error.close()
        return GeminiResult(None, reason)
    except (
        urllib.error.URLError,
        TimeoutError,
        KeyError,
        IndexError,
        TypeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        LOGGER.warning("Gemini request failed: %s", type(error).__name__)
        if isinstance(error, TimeoutError):
            reason = "Gemini phản hồi quá thời gian chờ."
        elif isinstance(error, urllib.error.URLError):
            reason = "Không kết nối được tới Gemini."
        else:
            reason = "Gemini trả về phản hồi không hợp lệ."
        return GeminiResult(None, reason)


def chat_answer(message):
    movies, showtimes, report = _cinema_data()
    database_answer = _database_answer(message, movies, showtimes, report)
    if database_answer:
        return _result(database_answer, "database")
    gemini_data = {"movies": [], "upcoming_showtimes": []}
    if GEMINI_SHARE_CINEMA_DATA:
        gemini_data = {
            "movies": [
                {
                    key: movie[key]
                    for key in ("title", "genre", "duration", "age", "status", "description")
                }
                for movie in movies
            ],
            "upcoming_showtimes": [
                {
                    key: showtime[key]
                    for key in (
                        "title",
                        "show_date",
                        "show_time",
                        "room",
                        "price",
                    )
                }
                for showtime in showtimes
                if showtime["show_date"] >= date.today().isoformat()
            ],
        }
    prompt = movie_prompt(gemini_data, message)
    completion = generate_with_gemini(prompt)
    if completion.answer:
        return _result(completion.answer, "gemini")
    return _result(
        "Tôi chưa thể trả lời câu hỏi mở này. Bạn có thể hỏi về thể loại, phim, "
        "suất chiếu, tình trạng ghế hoặc doanh thu để tra cứu dữ liệu Cinema.",
        "fallback",
        f"AI không khả dụng: {completion.error} Câu hỏi mở chưa được trả lời.",
    )


def recommendation_answer():
    movies, showtimes, _report = _cinema_data()
    active_movies = [
        movie for movie in movies if movie.get("status") == "Đang chiếu"
    ]
    if not active_movies:
        return _result("Hiện không có phim ở trạng thái Đang chiếu.", "database")
    return _result(
        "Gợi ý phim đang chiếu:\n" + "\n".join(
            _movie_line(movie, showtimes) for movie in active_movies
        ),
        "database",
    )


def report_answer():
    _movies, _showtimes, report = _cinema_data()
    return _result(
        f"Doanh thu từ vé đã thanh toán là {report['revenue']:,.0f} VNĐ. "
        f"Đã bán {report['ticket_count']} vé; hệ thống có "
        f"{report['movie_count']} phim và {report['show_count']} suất chiếu.",
        "database",
    )
