# Academic MCP chỉ đọc

`src/server.py` giữ vai trò khởi tạo MCP như bố cục ban đầu. `create_app()` chỉ lắp
server, tool và middleware; `register_tools()` khai báo 7 tool. Mỗi tool xác thực
người gọi rồi chuyển sang repository hoặc thuật toán, không chứa SQL hay xếp lịch.
Các module nghiệp vụ giữ riêng theo trách nhiệm:

| Module | Trách nhiệm |
| --- | --- |
| auth.py | Xác thực HTTP JWT độc lập với backend API |
| models.py | Kiểu dữ liệu học vụ và ràng buộc tham số |
| repository.py | Adapter SQLite chỉ đọc, SQL có tham số, allowlist cột |
| scheduling.py | Tính tổ hợp lớp không trùng lịch; không dùng LLM |

## Chạy local

Trong thư mục mcp, sau khi cài dependencies và tạo mcp/.env:

```bash
python -m scripts.seed_demo
python -m uvicorn src.server:create_app --factory --host 127.0.0.1 --port 8002
```

Seed chỉ tạo file mới `data/academic.sqlite3`, từ chối ghi đè. Dữ liệu hoàn toàn
GIẢ LẬP, không phải dữ liệu HUST. Sinh viên `demo-et` thuộc CTĐT DEMO-ET có ET101,
ET102 và môn chung MI101; `demo-it` thuộc DEMO-IT. Học kỳ ví dụ 2026-1. Học phần
đã chọn được lưu theo user và học kỳ, không dùng lại lựa chọn của kỳ trước.

MCP dùng Streamable HTTP ở `/mcp`, không phải REST endpoint đặt tên MCP. Token
được assistant ký bằng khóa ASSISTANT_MCP_SECRET, issuer assistant-api, audience
academic-mcp, role student, quyền academic:read; thời hạn tối đa 5 phút. Token web
không dùng trực tiếp ở đây. Mọi tool cá nhân lấy user từ request context đã xác thực,
không nhận user_id/role/SQL do model cung cấp. Không hỗ trợ stdio bỏ qua auth.

## Tools

| Tool | Công dụng |
| --- | --- |
| search_courses | Học phần, có tùy chọn chỉ lấy CTĐT của chính sinh viên |
| get_course | Tên, mã và tín chỉ học phần |
| list_open_classes | Lớp mở theo học kỳ/môn/CTĐT; có phân trang |
| search_lecturers | Hồ sơ công khai theo tên/đơn vị |
| get_lecturer | Hồ sơ, lớp giảng dạy và đánh giá tổng hợp đã công bố |
| get_my_academic_context | CTĐT, môn đã chọn và lớp đã đăng ký của người đang chat |
| suggest_timetable | Tính lịch từ môn cung cấp hoặc môn đã chọn; không đăng ký lớp |

Danh sách trả items và next_offset. Review chỉ là thống kê đã công bố, không có
nội dung riêng tư hoặc danh tính người đánh giá; demo dùng ngưỡng tối thiểu 5 đánh giá.
Đó là policy mẫu, không phải quy định nhà trường.

## Hợp đồng dữ liệu thật

- courses là học phần, một học phần có nhiều sections theo semester.
- curriculum liên kết phiên bản CTĐT với học phần: cả ET lẫn môn chung MI/FL/PE...
- students ánh xạ user đang chat tới CTĐT. Không suy ra CTĐT chỉ bằng tiền tố ET.
- selected_courses là môn đã chọn theo user/semester; enrollments là lớp đã đăng ký.
- meetings chứa đầy đủ thứ, giờ, tuần, phòng của một đơn vị đăng ký.
- lecturers/published_reviews chỉ chứa thông tin được phép công bố.

Thay repository.py khi nhóm có schema thật hoặc API học vụ. Dùng credentials chỉ
SELECT trên view/table cho phép, lọc chính xác dữ liệu cá nhân, có statement timeout.
Không đưa SQL do LLM sinh vào repository. Mode=ro/query_only bảo vệ adapter demo;
quyền trên DB thật vẫn phải thiết lập ở database.

## Xếp lịch

Một section đại diện một đơn vị đăng ký với đủ LT/BT/TH. Nếu các lớp con phải ghép
nhóm, adapter phải tạo tổ hợp hợp lệ nguyên khối trước khi gọi thuật toán.

Hai buổi xung đột khi cùng thứ, giao ít nhất một tuần và có giao khoảng giờ
[start,end). Vì vậy 08:00–09:30 và 09:30–11:00 không trùng. Quy ước weekday 0=thứ
Hai, 6=Chủ nhật; giờ theo phút từ 00:00; weeks là tuần học trong cùng học kỳ.
Thiếu lịch/tuần không được coi là lịch trống.

Thuật toán giữ lớp đã đăng ký, bỏ lớp đóng/đầy, lọc sở thích thứ/giờ, thử môn ít
lớp trước. Tối đa 8 môn, 30 lớp ứng viên/môn, 5 phương án, 10.000 trạng thái tìm kiếm.
Kết quả là phương án khả thi đầu tiên, chưa tối ưu khoảng trống hoặc số ngày học.
search_truncated cho biết chưa duyệt hết; hết ngân sách khác với không tồn tại lịch.

Chưa kiểm tra tiên quyết, giới hạn tín chỉ, lịch thi, lịch ngoại lệ hoặc di chuyển
liên cơ sở. Sở thích chỉ áp dụng lớp mới; lớp đã đăng ký được giữ nguyên. Backend
web phải kiểm tra lại trong transaction khi đăng ký thật vì số chỗ có thể thay đổi.

## Kiểm thử

```bash
python -m pytest tests -q
```

Chạy riêng với backend/tests vì hai process có package src riêng. Mọi fixture đều
là database tạm; tests không truy cập dữ liệu học vụ thật.
