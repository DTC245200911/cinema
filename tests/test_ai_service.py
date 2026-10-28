import asyncio
import json
import io
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from backend.app import database
from backend.app.config import SESSION_COOKIE_NAME
from backend.app.routers.ai import ai_chat, ai_recommend, ai_report
from backend.app.security import create_session, hash_password
from backend.app.services import ai


class AIServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.temp_dir.name) / "cinema-test.db"
        database.initialize_database()
        self.sessions = {}
        connection = database.get_connection()
        try:
            user_ids = {}
            for username, role in (
                ("test-admin", "Admin"),
                ("test-manager", "Quản lý"),
                ("test-cashier", "Thu ngân"),
            ):
                user_id = connection.execute(
                    """INSERT INTO users(username,password,role)
                       VALUES(?,?,?)""",
                    (username, hash_password("test-password"), role),
                ).lastrowid
                user_ids[username] = user_id
            connection.commit()
        finally:
            connection.close()
        self.sessions = {
            username: create_session(user_id)
            for username, user_id in user_ids.items()
        }

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def test_genre_suggestion_lists_actual_genres_and_matching_films(self):
        result = ai.chat_answer("gợi ý thể loại")

        self.assertEqual(result["source"], "database")
        self.assertIn("Hoạt hình: Inside Out 2", result["answer"])
        self.assertIn("Khoa học viễn tưởng: Dune: Part Two", result["answer"])

    def test_a_genre_only_question_returns_only_matching_movies(self):
        result = ai.chat_answer("hoạt hình")

        self.assertIn("Inside Out 2", result["answer"])
        self.assertNotIn("Dune: Part Two", result["answer"])
        self.assertIn("phân loại P", result["answer"])
        self.assertIn("Suất chiếu:", result["answer"])

    def test_genre_recommendation_is_filtered_by_requested_genre(self):
        result = ai.chat_answer("Gợi ý phim khoa học viễn tưởng")

        self.assertIn("Dune: Part Two", result["answer"])
        self.assertNotIn("Inside Out 2", result["answer"])

    def test_short_genre_name_matches_the_database_genre(self):
        result = ai.chat_answer("khoa học")

        self.assertEqual(result["source"], "database")
        self.assertIn("Dune: Part Two", result["answer"])
        self.assertNotIn("Inside Out 2", result["answer"])

    def test_movie_question_returns_database_details_and_screening(self):
        result = ai.chat_answer("Inside Out 2 chiếu ở đâu và lúc mấy giờ?")

        self.assertIn("Inside Out 2", result["answer"])
        self.assertIn("Hoạt hình", result["answer"])
        self.assertIn("Phòng 02", result["answer"])
        self.assertIn("VNĐ", result["answer"])

    def test_seat_question_reports_real_booked_and_available_seats(self):
        booked = ai.chat_answer("Dune: Part Two ghế A01 đã đặt chưa?")
        available = ai.chat_answer("Dune: Part Two ghế A03 còn trống không?")
        room = ai.chat_answer("Phòng 02 còn bao nhiêu ghế?")

        self.assertIn("A01 đã được đặt", booked["answer"])
        self.assertIn("A03 hiện còn trống", available["answer"])
        self.assertIn("còn 80/80 ghế", room["answer"])

    def test_schedule_question_returns_actual_screenings(self):
        result = ai.chat_answer("Lịch chiếu hiện có")

        self.assertIn("Lịch chiếu hiện có:", result["answer"])
        self.assertIn("Phòng 01", result["answer"])

    def test_today_schedule_filters_out_other_dates(self):
        result = ai.chat_answer("Lịch chiếu hôm nay")
        today = date.today().isoformat()
        expected_shows = [
            showtime for showtime in database.list_showtimes()
            if showtime["show_date"] == today
        ]

        if expected_shows:
            self.assertIn(today, result["answer"])
        else:
            self.assertIn("chưa có suất chiếu", result["answer"])

    def test_report_values_are_computed_from_database(self):
        result = ai.report_answer()
        report = database.dashboard_data()

        self.assertEqual(result["source"], "database")
        self.assertIn(f"{report['revenue']:,.0f} VNĐ", result["answer"])
        self.assertIn(f"{report['ticket_count']} vé", result["answer"])
        self.assertIn(f"{report['show_count']} suất chiếu", result["answer"])

    def test_recommendation_and_report_endpoints_return_flat_ai_results(self):
        admin_request = SimpleNamespace(cookies={
            SESSION_COOKIE_NAME: self.sessions["test-admin"]
        })

        recommendation = ai_recommend(admin_request)
        report = ai_report(admin_request)

        self.assertIsInstance(recommendation["answer"], str)
        self.assertEqual(recommendation["source"], "database")
        self.assertIsInstance(report["answer"], str)
        self.assertEqual(report["source"], "database")

    def test_chat_report_questions_are_restricted_to_management(self):
        async def cashier_request_json():
            return {"message": "Doanh thu"}

        cashier_request = SimpleNamespace(
            cookies={SESSION_COOKIE_NAME: self.sessions["test-cashier"]},
            json=cashier_request_json,
        )
        response = asyncio.run(ai_chat(cashier_request))

        self.assertEqual(response.status_code, 403)

    def test_management_can_request_report_through_chat(self):
        async def admin_request_json():
            return {"message": "Doanh thu"}

        admin_request = SimpleNamespace(
            cookies={SESSION_COOKIE_NAME: self.sessions["test-admin"]},
            json=admin_request_json,
        )
        response = asyncio.run(ai_chat(admin_request))

        self.assertEqual(response["source"], "database")
        self.assertIn("VNĐ", response["answer"])

    def test_forged_legacy_username_cookie_does_not_authenticate(self):
        response = ai_recommend(SimpleNamespace(
            cookies={"cinema_user": "admin"}
        ))

        self.assertEqual(response.status_code, 401)

    def test_unavailable_gemini_is_explained_for_open_questions(self):
        with patch.object(ai, "GEMINI_API_KEY", ""):
            result = ai.chat_answer("Hãy giải thích vì sao phim ảnh hấp dẫn")

        self.assertEqual(result["source"], "fallback")
        self.assertIn("chưa thể trả lời", result["answer"])
        self.assertIn("AI không khả dụng", result["notice"])
        self.assertIn("GEMINI_API_KEY", result["notice"])

    def test_gemini_receives_movies_showtimes_and_report_context(self):
        with patch.object(
            ai,
            "generate_with_gemini",
            return_value=ai.GeminiResult("Câu trả lời từ Gemini.", None),
        ) as generate, patch.object(ai, "GEMINI_SHARE_CINEMA_DATA", True):
            result = ai.chat_answer("Hãy nêu một nhận xét chung")

        self.assertEqual(result["source"], "gemini")
        self.assertEqual(result["answer"], "Câu trả lời từ Gemini.")
        prompt = generate.call_args.args[0]
        self.assertIn("showtimes", prompt)
        self.assertIn("Dune: Part Two", prompt)
        self.assertIn("Phòng 01", prompt)
        self.assertNotIn("revenue_vnd", prompt)
        self.assertNotIn("sold_seats", prompt)

    def test_gemini_does_not_receive_cinema_data_by_default(self):
        with patch.object(
            ai,
            "generate_with_gemini",
            return_value=ai.GeminiResult("Câu trả lời chung.", None),
        ) as generate, patch.object(ai, "GEMINI_SHARE_CINEMA_DATA", False):
            ai.chat_answer("Giải thích điện ảnh")

        prompt = generate.call_args.args[0]
        self.assertNotIn("Dune: Part Two", prompt)
        self.assertNotIn("revenue_vnd", prompt)
        self.assertNotIn("sold_seats", prompt)

    def test_gemini_http_errors_return_a_safe_reason(self):
        error = urllib.error.HTTPError(
            "https://example.invalid", 429, "rate limited", {}, io.BytesIO()
        )
        with patch.object(ai, "GEMINI_API_KEY", "test-key"), patch.object(
            ai.urllib.request, "urlopen", side_effect=error
        ):
            result = ai.generate_with_gemini("test prompt")

        self.assertIsNone(result.answer)
        self.assertIn("giới hạn lượt gọi", result.error)
        self.assertNotIn("test-key", result.error)

    def test_gemini_request_uses_separate_system_instruction(self):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "candidates": [{
                "content": {"parts": [{"text": "Dựa trên dữ liệu."}]}
            }]
        }).encode()
        response.__enter__.return_value = response
        with patch.object(ai, "GEMINI_API_KEY", "test-key"), patch.object(
            ai.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            result = ai.generate_with_gemini("Dữ liệu Cinema")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(result.answer, "Dựa trên dữ liệu.")
        self.assertNotIn("key=", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        self.assertIn("không bịa hoặc suy đoán", payload["systemInstruction"]["parts"][0]["text"])
        self.assertIn("Dữ liệu Cinema", payload["contents"][0]["parts"][0]["text"])


if __name__ == "__main__":
    unittest.main()
