import argparse
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

from backend.app.config import BACKUP_DIR, DATABASE_PATH

REQUIRED_TABLES = {
    "users",
    "movies",
    "rooms",
    "showtimes",
    "tickets",
    "ticket_seats",
    "audit_logs",
}


def _backup_filename(directory, prefix):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return directory / f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}.sqlite3"


def create_backup_from_connection(connection, directory=BACKUP_DIR, prefix="backup"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination_path = _backup_filename(directory, prefix)
    destination = sqlite3.connect(destination_path)
    try:
        connection.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Bản sao lưu không toàn vẹn: {integrity}")
    except Exception:
        destination.close()
        destination_path.unlink(missing_ok=True)
        raise
    destination.close()
    return destination_path


def create_backup(database_path=DATABASE_PATH, directory=BACKUP_DIR):
    database_path = Path(database_path)
    if not database_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy CSDL: {database_path}")
    source = sqlite3.connect(database_path)
    try:
        return create_backup_from_connection(source, directory)
    finally:
        source.close()


def validate_backup(backup_path):
    backup_path = Path(backup_path)
    if not backup_path.is_file() or backup_path.stat().st_size == 0:
        raise ValueError("Tệp sao lưu không tồn tại hoặc đang rỗng.")
    connection = sqlite3.connect(f"{backup_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"CSDL sao lưu bị lỗi toàn vẹn: {integrity}")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise ValueError("CSDL sao lưu có quan hệ khóa ngoại không hợp lệ.")
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing_tables = REQUIRED_TABLES - tables
        if missing_tables:
            raise ValueError(
                "Tệp không phải bản sao lưu Cinema hợp lệ; thiếu bảng: "
                + ", ".join(sorted(missing_tables))
            )
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            raise ValueError("Bản sao lưu chưa dùng schema Cinema phiên bản 2 trở lên.")
        return backup_path
    except sqlite3.DatabaseError as exc:
        raise ValueError("Tệp không phải cơ sở dữ liệu SQLite hợp lệ.") from exc
    finally:
        connection.close()


def restore_database(backup_path, database_path=DATABASE_PATH, directory=BACKUP_DIR):
    backup_path = validate_backup(backup_path)
    database_path = Path(database_path)
    if backup_path.resolve() == database_path.resolve():
        raise ValueError("Tệp nguồn sao lưu không được trùng CSDL đang chạy.")

    safety_backup = None
    if database_path.is_file():
        safety_backup = create_backup(database_path, directory)

    source = sqlite3.connect(backup_path)
    destination = sqlite3.connect(database_path)
    try:
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = destination.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError("Khôi phục thất bại do kiểm tra toàn vẹn CSDL.")
    except Exception:
        if safety_backup:
            destination.rollback()
            safety = sqlite3.connect(safety_backup)
            try:
                safety.backup(destination)
            finally:
                safety.close()
        raise
    finally:
        source.close()
        destination.close()
    return safety_backup


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sao lưu/khôi phục CSDL Cinema.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup", help="Tạo bản sao lưu CSDL hiện tại.")
    restore_parser = commands.add_parser("restore", help="Khôi phục từ bản sao lưu.")
    restore_parser.add_argument("file", type=Path, help="Đường dẫn tệp .sqlite3")
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "backup":
            result = create_backup()
            print(f"Đã sao lưu CSDL: {result}")
        else:
            safety_backup = restore_database(arguments.file)
            print("Đã khôi phục CSDL thành công.")
            if safety_backup:
                print(f"Bản sao lưu trước khôi phục: {safety_backup}")
    except (OSError, ValueError, sqlite3.DatabaseError, RuntimeError) as exc:
        print(f"Lỗi sao lưu/khôi phục: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
