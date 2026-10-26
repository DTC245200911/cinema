# Hệ thống quản lý rạp chiếu phim có tích hợp AI

Demo Python/FastAPI dành cho bài kiểm tra thường xuyên 2.

## Công nghệ
- Backend: Python + FastAPI
- Giao diện: HTML + CSS + JavaScript
- Database demo: SQLite
- AI: Google Gemini REST API (có chế độ demo nếu chưa cấu hình API key)

## Chức năng
1. Đăng nhập và phân quyền Admin / Quản lý / Thu ngân.
2. CRUD phim.
3. Tìm kiếm phim.
4. Quản lý suất chiếu.
5. Bán vé và chọn ghế.
6. Dashboard doanh thu, vé bán, tỷ lệ lấp đầy.
7. Chatbot AI tư vấn phim/lịch chiếu.
8. AI gợi ý phim.
9. AI tóm tắt báo cáo doanh thu.
10. Xử lý dữ liệu lỗi cơ bản.

## Chạy dự án

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Mở: http://127.0.0.1:8000

## Tài khoản demo
- Admin: admin / 123456
- Quản lý: manager / 123456
- Thu ngân: cashier / 123456

## AI thật
Tạo file `.env` từ `.env.example` và điền:
GEMINI_API_KEY=...

Nếu không có API key, hệ thống vẫn chạy với câu trả lời AI demo để dễ trình diễn trên máy.
