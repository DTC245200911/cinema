from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.app.security import current_user, management_user
from backend.app.services.ai import (
    chat_answer,
    is_report_question,
    recommendation_answer,
    report_answer,
)

router = APIRouter()


@router.post("/api/ai")
async def ai_chat(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "Chưa đăng nhập"}, status_code=401)
    try:
        data = await request.json()
    except ValueError:
        return JSONResponse(
            {"error": "Dữ liệu yêu cầu không hợp lệ."},
            status_code=400,
        )
    if not isinstance(data, dict):
        return JSONResponse(
            {"error": "Dữ liệu yêu cầu không đúng định dạng."},
            status_code=400,
        )
    message = data.get("message")
    if not isinstance(message, str):
        return JSONResponse(
            {"error": "Câu hỏi phải là văn bản."},
            status_code=400,
        )
    message = message.strip()
    if not message:
        return JSONResponse({"error": "Hãy nhập câu hỏi."}, status_code=400)
    if len(message) > 1000:
        return JSONResponse(
            {"error": "Câu hỏi không được dài quá 1000 ký tự."},
            status_code=400,
        )
    if is_report_question(message) and user["role"] not in {
        "Admin",
        "Quản lý",
    }:
        return JSONResponse(
            {"error": "Chỉ Quản lý/Admin được xem dữ liệu doanh thu và báo cáo."},
            status_code=403,
        )
    return chat_answer(message)


@router.get("/api/ai/recommend")
def ai_recommend(request: Request):
    if not current_user(request):
        return JSONResponse({"error": "Chưa đăng nhập"}, status_code=401)
    return recommendation_answer()


@router.get("/api/ai/report")
def ai_report(request: Request):
    if not management_user(request):
        return JSONResponse(
            {"error": "Chỉ Quản lý/Admin được xem báo cáo AI."},
            status_code=403,
        )
    return report_answer()
