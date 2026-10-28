import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import database
from backend.app.config import SESSION_COOKIE_NAME, SESSION_TTL_SECONDS
from backend.app.main import create_app
from backend.app.routers.pages import home, login
from backend.app.security import (
    BOOTSTRAP_PASSWORDS,
    configure_bootstrap_accounts,
    create_session,
    current_user,
    hash_password,
    revoke_session,
    verify_password,
)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.temp_dir.name) / "cinema-test.db"
        database.initialize_database()

    def tearDown(self):
        database.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def create_user(self, username="secure-admin", role="Admin"):
        connection = database.get_connection()
        try:
            user_id = connection.execute(
                """INSERT INTO users(username,password,role)
                   VALUES(?,?,?)""",
                (username, hash_password("a-long-test-password"), role),
            ).lastrowid
            connection.commit()
            return user_id
        finally:
            connection.close()

    def test_session_token_is_opaque_hashed_expiring_and_revocable(self):
        user_id = self.create_user()
        token = create_session(user_id)
        request = SimpleNamespace(cookies={SESSION_COOKIE_NAME: token})

        self.assertIsNotNone(current_user(request))

        connection = database.get_connection()
        try:
            session = connection.execute(
                "SELECT token_hash,created_at,expires_at FROM user_sessions"
            ).fetchone()
            self.assertNotEqual(session["token_hash"], token)
            self.assertEqual(len(session["token_hash"]), 64)
            self.assertGreater(session["expires_at"], session["created_at"])
            self.assertLessEqual(
                session["expires_at"] - session["created_at"],
                SESSION_TTL_SECONDS,
            )
        finally:
            connection.close()

        revoke_session(token)
        self.assertIsNone(current_user(request))

    def test_expired_or_forged_session_cookie_is_rejected(self):
        user_id = self.create_user()
        token = create_session(user_id)
        connection = database.get_connection()
        try:
            connection.execute(
                "UPDATE user_sessions SET expires_at=0"
            )
            connection.commit()
        finally:
            connection.close()
        self.assertIsNone(current_user(SimpleNamespace(
            cookies={SESSION_COOKIE_NAME: token}
        )))
        self.assertIsNone(current_user(SimpleNamespace(
            cookies={SESSION_COOKIE_NAME: "not-a-session-token"}
        )))
        self.assertIsNone(current_user(SimpleNamespace(
            cookies={"cinema_user": "admin"}
        )))

    def test_login_sets_secure_http_only_cookie_with_lifetime(self):
        user_id = self.create_user()
        request = SimpleNamespace(cookies={})
        connection = database.get_connection()
        try:
            connection.execute(
                "UPDATE users SET username='admin',role='Admin' WHERE id=?",
                (user_id,),
            )
            connection.commit()
        finally:
            connection.close()

        response = login(request, "admin", "a-long-test-password")
        set_cookie = response.headers["set-cookie"].lower()

        self.assertIn("httponly", set_cookie)
        self.assertIn("secure", set_cookie)
        self.assertIn("samesite=lax", set_cookie)
        self.assertIn(f"max-age={SESSION_TTL_SECONDS}", set_cookie)

    def test_cashier_dashboard_does_not_render_financial_data(self):
        from starlette.requests import Request

        user_id = self.create_user("test-cashier", "Thu ngân")
        token = create_session(user_id)
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [
                (b"cookie", f"{SESSION_COOKIE_NAME}={token}".encode())
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
            "root_path": "",
        }

        response = home(Request(scope))
        html = response.body.decode("utf-8")

        self.assertNotIn("Tổng doanh thu mẫu", html)
        self.assertNotIn("150,000", html)
        self.assertIn("const chartValues = [];", html)

    def test_cross_origin_mutating_request_is_rejected(self):
        application = create_app()
        messages = []
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/ai",
            "raw_path": b"/api/ai",
            "query_string": b"",
            "headers": [
                (b"host", b"cinema.local"),
                (b"origin", b"https://attacker.example"),
                (b"content-length", b"0"),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("cinema.local", 8000),
            "root_path": "",
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        asyncio.run(application(scope, receive, send))

        response_start = next(
            message for message in messages
            if message["type"] == "http.response.start"
        )
        self.assertEqual(response_start["status"], 403)

    def test_legacy_demo_passwords_are_disabled_until_bootstrapped(self):
        connection = database.get_connection()
        try:
            connection.execute(
                "INSERT INTO users(username,password,role) VALUES(?,?,?)",
                ("admin", hash_password("123456"), "Admin"),
            )
            connection.commit()
            with patch.dict(
                BOOTSTRAP_PASSWORDS,
                {"admin": "", "manager": "", "cashier": ""},
                clear=True,
            ):
                configure_bootstrap_accounts(connection)
                stored = connection.execute(
                    "SELECT password FROM users WHERE username='admin'"
                ).fetchone()["password"]
                self.assertFalse(verify_password("123456", stored))
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM credential_bootstrap"
                    ).fetchone()[0],
                    1,
                )

                with patch.dict(
                    BOOTSTRAP_PASSWORDS,
                    {
                        "admin": "a-new-secure-password",
                        "manager": "",
                        "cashier": "",
                    },
                    clear=True,
                ):
                    configure_bootstrap_accounts(connection)
                stored = connection.execute(
                    "SELECT password FROM users WHERE username='admin'"
                ).fetchone()["password"]
                self.assertTrue(verify_password("a-new-secure-password", stored))
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM credential_bootstrap"
                    ).fetchone()[0],
                    0,
                )
        finally:
            connection.close()

    def test_bootstrap_passwords_must_be_long_enough(self):
        connection = database.get_connection()
        try:
            with patch.dict(
                BOOTSTRAP_PASSWORDS,
                {"admin": "short", "manager": "", "cashier": ""},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "at least 12"):
                    configure_bootstrap_accounts(connection)
        finally:
            connection.close()

    def test_configured_bootstrap_password_creates_admin_without_default(self):
        connection = database.get_connection()
        try:
            with patch.dict(
                BOOTSTRAP_PASSWORDS,
                {
                    "admin": "a-new-secure-password",
                    "manager": "",
                    "cashier": "",
                },
                clear=True,
            ):
                configure_bootstrap_accounts(connection)

            admin = connection.execute(
                "SELECT password FROM users WHERE username='admin'"
            ).fetchone()
            self.assertIsNotNone(admin)
            self.assertTrue(
                verify_password("a-new-secure-password", admin["password"])
            )
            self.assertFalse(verify_password("123456", admin["password"]))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
