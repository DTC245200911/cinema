import sqlite3
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from backend.app.backup import create_backup, restore_database
from backend.app.config import BACKUP_DIR, DATABASE_PATH
from backend.app.security import current_user, log_action

router = APIRouter()
MAX_BACKUP_UPLOAD_BYTES = 100 * 1024 * 1024
ALLOWED_BACKUP_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def _admin_user(request):
    user = current_user(request)
    return user if user and user["role"] == "Admin" else None


@router.get("/api/admin/database/backup")
def download_database_backup(request: Request):
    user = _admin_user(request)
    if not user:
        return JSONResponse(
            {"error": "Chỉ Admin được sao lưu cơ sở dữ liệu."},
            status_code=403,
        )
    try:
        backup_path = create_backup()
    except (OSError, sqlite3.DatabaseError, RuntimeError) as exc:
        return JSONResponse(
            {"error": f"Không thể tạo bản sao lưu: {exc}"},
            status_code=500,
        )
    log_action(user["username"], "Tạo bản sao lưu cơ sở dữ liệu")
    return FileResponse(
        backup_path,
        media_type="application/vnd.sqlite3",
        filename=backup_path.name,
    )


@router.post("/api/admin/database/restore")
async def upload_database_restore(
    request: Request,
    file: UploadFile = File(...),
):
    user = _admin_user(request)
    if not user:
        return JSONResponse(
            {"error": "Chỉ Admin được khôi phục cơ sở dữ liệu."},
            status_code=403,
        )
    if Path(file.filename or "").suffix.lower() not in ALLOWED_BACKUP_SUFFIXES:
        return JSONResponse(
            {"error": "Chỉ chấp nhận tệp .db, .sqlite hoặc .sqlite3."},
            status_code=400,
        )

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".sqlite3",
            prefix="restore-upload-",
            dir=BACKUP_DIR,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BACKUP_UPLOAD_BYTES:
                    return JSONResponse(
                        {"error": "Tệp sao lưu vượt quá giới hạn 100 MB."},
                        status_code=413,
                    )
                temporary_file.write(chunk)
        safety_backup = restore_database(
            temporary_path,
            database_path=DATABASE_PATH,
            directory=BACKUP_DIR,
        )
    except (OSError, ValueError, sqlite3.DatabaseError, RuntimeError) as exc:
        return JSONResponse(
            {"error": f"Không thể khôi phục CSDL: {exc}"},
            status_code=400,
        )
    finally:
        await file.close()
        if temporary_path:
            temporary_path.unlink(missing_ok=True)

    log_action(user["username"], "Khôi phục cơ sở dữ liệu")
    return {
        "ok": True,
        "message": "Đã khôi phục CSDL. Hãy tải lại trang.",
        "safety_backup": safety_backup.name if safety_backup else None,
    }
