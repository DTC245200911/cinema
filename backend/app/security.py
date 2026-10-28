import hashlib
import secrets
import sqlite3
from datetime import datetime
from time import time

from backend.app.config import (
    BOOTSTRAP_PASSWORDS,
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
)
from backend.app.database import get_connection


MANAGEMENT_ROLES = {"Admin", "Quản lý"}
DEFAULT_ACCOUNTS = {
    "admin": "Admin",
    "manager": "Quản lý",
    "cashier": "Thu ngân",
}


def hash_password(password):
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000)
    return salt.hex() + ":" + key.hex()


def verify_password(password, stored):
    try:
        salt, key = stored.split(":")
        check = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), 120000
        )
        return secrets.compare_digest(check.hex(), key)
    except (ValueError, TypeError):
        return False


def configure_bootstrap_accounts(connection):
    for username, password in BOOTSTRAP_PASSWORDS.items():
        if password and len(password) < 12:
            raise ValueError(
                f"CINEMA_{username.upper()}_PASSWORD must be at least 12 characters."
            )
    rows = {
        row["username"]: row
        for row in connection.execute(
            "SELECT id,username,password,role FROM users"
        ).fetchall()
    }
    if not rows:
        for username, role in DEFAULT_ACCOUNTS.items():
            password = BOOTSTRAP_PASSWORDS[username]
            if password:
                connection.execute(
                    "INSERT INTO users(username,password,role) VALUES(?,?,?)",
                    (username, hash_password(password), role),
                )
        return

    for username, role in DEFAULT_ACCOUNTS.items():
        row = rows.get(username)
        if not row:
            password = BOOTSTRAP_PASSWORDS[username]
            if password:
                connection.execute(
                    "INSERT INTO users(username,password,role) VALUES(?,?,?)",
                    (username, hash_password(password), role),
                )
            continue
        if row["role"] != role:
            continue
        if verify_password("123456", row["password"]):
            cursor = connection.execute(
                """INSERT OR IGNORE INTO credential_bootstrap(username,user_id)
                   VALUES(?,?)""",
                (username, row["id"]),
            )
            if cursor.rowcount:
                connection.execute(
                    "UPDATE users SET password=? WHERE id=?",
                    (hash_password(secrets.token_urlsafe(32)), row["id"]),
                )

    pending = connection.execute(
        "SELECT username,user_id FROM credential_bootstrap"
    ).fetchall()
    for row in pending:
        password = BOOTSTRAP_PASSWORDS.get(row["username"], "")
        if not password:
            continue
        connection.execute(
            "UPDATE users SET password=? WHERE id=?",
            (hash_password(password), row["user_id"]),
        )
        connection.execute(
            "DELETE FROM credential_bootstrap WHERE username=?",
            (row["username"],),
        )


def create_session(user_id):
    now = int(time())
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM user_sessions WHERE expires_at<=?", (now,))
        connection.execute(
            """INSERT INTO user_sessions(user_id,token_hash,created_at,expires_at)
               VALUES(?,?,?,?)""",
            (user_id, token_hash, now, now + SESSION_TTL_SECONDS),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()
    return token


def revoke_session(token):
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = get_connection()
    try:
        connection.execute(
            "DELETE FROM user_sessions WHERE token_hash=?", (token_hash,)
        )
        connection.commit()
    finally:
        connection.close()


def current_user(request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or len(token) > 128:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = get_connection()
    try:
        row = connection.execute(
            """SELECT u.id,u.username,u.role
               FROM user_sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.expires_at>?""",
            (token_hash, int(time())),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def management_user(request):
    user = current_user(request)
    if not user or user["role"] not in MANAGEMENT_ROLES:
        return None
    return user


def log_action(username, action):
    connection = get_connection()
    try:
        connection.execute(
            "INSERT INTO audit_logs(username,action,created_at) VALUES(?,?,?)",
            (username, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        connection.commit()
    finally:
        connection.close()
