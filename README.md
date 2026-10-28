# Cinema AI - Hệ thống quản lý rạp chiếu phim

Demo Python/FastAPI dành cho bài kiểm tra thường xuyên 2. Backend và frontend
được tổ chức thành các thư mục riêng; backend tiếp tục phục vụ giao diện để demo
chỉ cần chạy một ứng dụng.

## Kiến trúc dự án

```text
backend/
  app/
    main.py                 # Khởi tạo FastAPI, static files và routers
    config.py               # Đường dẫn dự án và cấu hình môi trường
    database.py             # Kết nối SQLite, khởi tạo và truy vấn dữ liệu
    backup.py               # Kiểm tra, sao lưu và khôi phục CSDL
    security.py             # Xác thực, phân quyền và audit log
    prompts.py              # Prompt cho các tính năng AI
    routers/
      pages.py              # Trang chủ, đăng nhập, đăng xuất
      movies.py             # API quản lý phim
      showtimes.py          # API suất chiếu, phòng chỉ đọc và sơ đồ ghế
      tickets.py            # API bán vé
      ai.py                 # API chat, gợi ý và báo cáo AI
      maintenance.py        # API sao lưu/khôi phục dành riêng cho Admin
    services/
      cinema.py             # Quy tắc nghiệp vụ phim chiếu và ghế
      ai.py                 # Tích hợp Gemini và chế độ demo
frontend/
  templates/                # Giao diện HTML/Jinja
  static/                   # CSS và tài nguyên trình duyệt
tests/                      # Kiểm thử nghiệp vụ bằng unittest
backups/                    # Bản sao lưu tự động/thủ công (không commit)
main.py                     # Điểm vào tương thích với lệnh chạy cũ
cinema.db                   # Cơ sở dữ liệu SQLite demo
```

### Luồng xử lý

Trình duyệt tải trang HTML/CSS từ `frontend/`; JavaScript gọi các API FastAPI
trong `backend/app/routers/`. Router kiểm tra đăng nhập/phân quyền, sau đó dùng
các hàm truy vấn trong `database.py` hoặc quy tắc trong `services/`. SQLite
được giữ ở thư mục gốc để không làm thay đổi vị trí dữ liệu demo hiện tại.

## Công nghệ

- Backend: Python + FastAPI
- Frontend: HTML/Jinja + CSS + JavaScript
- Cơ sở dữ liệu demo: SQLite
- AI: tra cứu dữ liệu Cinema trực tiếp; Google Gemini REST API cho câu hỏi mở

## Chạy dự án trên Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn backend.app.main:app --reload
```

Hoặc chạy `run_server.bat` / `run_server.ps1`. Ứng dụng mở tại
http://127.0.0.1:8000.

Lệnh chạy cũ `python -m uvicorn main:app --reload` vẫn được hỗ trợ.

## Kiểm thử

Chạy các kiểm thử nghiệp vụ bằng thư viện chuẩn Python:

```powershell
python -m unittest discover -s tests -v
```

Kiểm thử bao gồm migration dữ liệu cũ, ghế trùng/vượt sức chứa, lịch chiếu chồng
lấn, phòng cố định, sao lưu/khôi phục, xác minh mật khẩu và câu trả lời AI đối
chiếu trực tiếp với dữ liệu SQLite.

## Toàn vẹn dữ liệu, sao lưu và khôi phục

- Ghế được chuẩn hóa vào bảng `ticket_seats`, mỗi ghế gắn với vé và suất chiếu.
  Khóa chính duy nhất theo `(showtime_id, seat_index)` ngăn hai vé giữ cùng ghế;
  trigger SQLite chặn ghế vượt sức chứa. Giao dịch bán vé ghi vé và toàn bộ ghế
  trong cùng một transaction.
- Index và trigger ở SQLite chặn suất chiếu trùng phòng/giờ hoặc chồng lấn theo
  thời lượng phim, kể cả khi dữ liệu được ghi ngoài API. Phòng mẫu được khóa cấu
  hình tại DB để sức chứa không bị đổi hoặc phòng bị xóa.
- Khi khởi động trên CSDL cũ, ứng dụng tự tạo bản sao `pre-seat-migration-*`
  trước khi chuyển dữ liệu ghế từ cột cũ. Nếu dữ liệu cũ có ghế trùng hoặc sai
  sức chứa, migration dừng rõ ràng và giữ bản sao để xử lý.
- Admin có thể tải/khôi phục CSDL trong mục **Báo cáo**. Bản khôi phục được kiểm
  tra SQLite integrity, khóa ngoại và phiên bản schema; trước khi thay thế, hệ
  thống tự lưu bản an toàn hiện tại. Chỉ nhận tệp CSDL Cinema hợp lệ tối đa
  100 MB.
- Có thể thao tác từ dòng lệnh:

  ```powershell
  python -m backend.app.backup backup
  python -m backend.app.backup restore .\backups\backup-YYYYMMDD-HHMMSS-xxxxxxxx.sqlite3
  ```

- Mặc định các bản sao lưu nằm trong `backups/`. Có thể cấu hình `CINEMA_BACKUP_DIR`;
  đường dẫn CSDL cũng có thể đổi bằng `CINEMA_DB_PATH`.

## Tài khoản và mật khẩu bootstrap

Không còn tài khoản/mật khẩu mặc định. Đặt mật khẩu bootstrap trước khi chạy lần
đầu; mật khẩu phải dài ít nhất 12 ký tự:

```text
CINEMA_ADMIN_PASSWORD=replace-with-a-random-password-of-at-least-12-characters
# Optional accounts, when needed:
CINEMA_MANAGER_PASSWORD=replace-with-a-different-random-password
CINEMA_CASHIER_PASSWORD=replace-with-another-random-password
```

Chỉ cần cấu hình tài khoản muốn tạo. Với CSDL cũ, ứng dụng tự vô hiệu hóa tài
khoản demo dùng mật khẩu `123456`; đặt biến tương ứng rồi khởi động lại để đặt
mật khẩu mới. Các tài khoản hiện có với mật khẩu không phải mật khẩu demo được
giữ nguyên. Không commit file `.env`.

Đăng nhập tạo session ngẫu nhiên phía máy chủ; cookie chỉ chứa token không thể
đoán, còn CSDL chỉ lưu hash token. Session hết hạn sau 8 giờ, có thể thu hồi khi
đăng xuất, và cookie bật `HttpOnly`, `SameSite=Lax`, `Secure` theo mặc định.
Khi chạy HTTP trên môi trường local không hỗ trợ cookie Secure, có thể đặt
`SESSION_COOKIE_SECURE=false` chỉ cho môi trường phát triển; triển khai thật
phải dùng HTTPS và giữ Secure bật. Các request thay đổi dữ liệu có Origin/Referer
khác host bị từ chối.

Doanh thu, số vé và báo cáo chỉ hiển thị cho Admin/Quản lý; quyền được kiểm tra
ở API chat, API báo cáo và giao diện dashboard.

## Cấu hình AI thật

Các câu hỏi về thể loại, phim, lịch chiếu và doanh thu được trả lời trực tiếp
từ CSDL để đảm bảo đúng dữ liệu và không phụ thuộc vào mô hình. Câu hỏi mở mới
được gửi tới Google Gemini. Theo mặc định, chỉ nội dung câu hỏi được gửi; không
gửi danh mục phim, lịch chiếu, tình trạng ghế, vé hay số liệu doanh thu. Nếu
quản trị viên chấp thuận gửi danh mục phim và lịch chiếu sắp tới không kèm dữ
liệu bán vé, đặt `GEMINI_SHARE_CINEMA_DATA=true`. Bật tùy chọn này sẽ chia sẻ
các trường phim cơ bản, phòng, ngày/giờ và giá vé với Google; không bao giờ gửi
doanh thu, thông tin khách hàng hoặc ghế đã bán trong ngữ cảnh chat Gemini.
Giao diện thông báo rõ chế độ chia sẻ hiện tại.

API key được gửi trong header HTTPS tới Gemini, không còn gắn vào URL. Nếu key
cũ từng được sử dụng với phiên bản gửi key trên URL, hãy thu hồi/đổi key trong
Google AI Studio rồi cập nhật `.env`. Nếu Gemini hết thời gian chờ hoặc từ chối
yêu cầu, giao diện giải thích lý do.

Tạo file `.env` tại thư mục gốc và khai báo:

```text
GEMINI_API_KEY=your-api-key
GEMINI_MODEL=gemini-2.5-flash
GEMINI_SHARE_CINEMA_DATA=false
SESSION_COOKIE_SECURE=true
CINEMA_ADMIN_PASSWORD=replace-with-a-random-password-of-at-least-12-characters
```
Khi chưa có API key, câu hỏi tra cứu từ CSDL vẫn hoạt động bình thường; câu hỏi
mở sẽ được báo rõ là Gemini chưa khả dụng. Không commit file `.env`.
