import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
TEMPLATES_DIR = FRONTEND_DIR / "templates"
STATIC_DIR = FRONTEND_DIR / "static"
BACKUP_DIR = Path(os.getenv("CINEMA_BACKUP_DIR", PROJECT_ROOT / "backups"))

load_dotenv(PROJECT_ROOT / ".env")

DATABASE_PATH = Path(os.getenv("CINEMA_DB_PATH", PROJECT_ROOT / "cinema.db"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_SHARE_CINEMA_DATA = os.getenv(
    "GEMINI_SHARE_CINEMA_DATA", "false"
).strip().lower() in {"1", "true", "yes", "on"}
SESSION_COOKIE_NAME = "cinema_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
SESSION_COOKIE_SECURE = os.getenv(
    "SESSION_COOKIE_SECURE", "true"
).strip().lower() in {"1", "true", "yes", "on"}
BOOTSTRAP_PASSWORDS = {
    "admin": os.getenv("CINEMA_ADMIN_PASSWORD", ""),
    "manager": os.getenv("CINEMA_MANAGER_PASSWORD", ""),
    "cashier": os.getenv("CINEMA_CASHIER_PASSWORD", ""),
}
