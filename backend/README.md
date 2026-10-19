# Backend assistant

## Cài đặt và chạy

Python 3.10 trở lên. Môi trường người dùng cung cấp là
`/home/fil/miniconda3/envs/vllm` (Python 3.10, MCP 1.26, LangGraph 1.0.7).
Nếu tạo môi trường riêng, từ thư mục gốc chạy:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp backend/.env.example backend/.env
cp mcp/.env.example mcp/.env
```

Tạo hai secret ngẫu nhiên KHÁC NHAU bằng lệnh sau (chạy hai lần):

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

- `ASSISTANT_AUTH_SECRET`: backend web và assistant API cùng biết.
- `ASSISTANT_MCP_SECRET`: assistant API và Academic MCP cùng biết.

Mỗi secret ít nhất 32 ký tự. Thay các placeholder trong `.env.example`; không đưa
secret/token vào Git, browser hoặc prompt. `backend/config.yaml` chỉ chứa tham số
công khai. Environment có ưu tiên cao hơn `.env`, rồi đến YAML và mặc định.
Ví dụ `ASSISTANT_LLM__API_BASE_URL` ghi đè `llm.api_base_url`.

Chạy MCP theo [hướng dẫn](../mcp/README.md). Trong terminal khác, từ thư mục gốc:

```bash
cd backend
python -m uvicorn src.main:app --host 127.0.0.1 --port 8080
```

Qwen/vLLM cần chạy riêng theo `llm.api_base_url`, hỗ trợ tool calling và JSON mode.
Tên model mặc định là `qwen35-27b-fp8`; chỉnh cho khớp server thực tế. Không tự tải
model khi import hoặc khởi động app. Nếu server không nhận `chat_template_kwargs`,
đặt `ASSISTANT_LLM__SEND_CHAT_TEMPLATE_KWARGS=false`.

`GET /health` là liveness. Swagger ở `/docs`. Hai service đều dùng package `src`,
vì vậy phải chạy trong đúng thư mục và kiểm thử bằng hai process riêng.

## API và danh tính

Backend web đăng nhập người dùng, lấy role từ account rồi ký JWT HS256 với claims:

```json
{
  "sub": "demo-et",
  "role": "student",
  "permissions": ["academic:read"],
  "iss": "web-backend",
  "aud": "assistant-api",
  "iat": 0,
  "exp": 300
}
```

`iat`/`exp` phải là Unix timestamp thật; ví dụ trên chỉ mô tả cấu trúc. Thời hạn
tối đa 15 phút. `sub` ánh xạ tới hồ sơ học vụ. Không lấy role từ body hoặc header
X-Role/X-User-Id. Với local, mô phỏng backend web trong thư mục backend:

```bash
python -m scripts.mint_demo_token --user demo-et
```

Gán token vừa in vào biến shell `TOKEN` của bạn rồi gọi:

```bash
curl -N http://127.0.0.1:8080/api/v1/chat/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Các lớp mở thuộc CTĐT của tôi trong học kỳ 2026-1?"}'
```

`POST /api/v1/chat` cùng body nhưng trả JSON cuối. Body chỉ có `message` và
`session_id` tùy chọn; bỏ session_id để tạo UUID mới. Giữ UUID trả về để nối chat.
Không nhận lịch sử do client gửi. Hội thoại luôn được kiểm tra owner/role ở server.

SSE gửi `status` trong khi xử lý, sau đó `answer` đã qua guardrails và lưu lịch sử,
rồi `done`. Không phát bản nháp, token model, suy luận hoặc tool payload ra UI.

```text
event: status
data: {"stage":"checking_input"}

event: answer
data: {"response_id":"...","session_id":"...","response":"...","status":"answered"}

event: done
data: {"ok":true,"session_id":"..."}
```

Heartbeat `: ping` mỗi 10 giây khi chờ. Lỗi sau HTTP 200 gửi `error` an toàn rồi
`done` với ok=false. Client ngắt stream sẽ hủy tác vụ và giải phóng lease. Không
hỗ trợ replay/Last-Event-ID; mất kết nối sau commit thì lịch sử có thể đã được lưu.

| HTTP trước stream | Ý nghĩa |
| --- | --- |
| 401 | Token thiếu/sai/hết hạn/sai issuer hoặc audience |
| 403 | Sai quyền hoặc không sở hữu hội thoại |
| 409 | Hội thoại đang xử lý lượt khác |
| 422 | Body sai schema, thừa trường, nội dung rỗng hoặc quá dài |
| 501 | Manager chưa triển khai |

JSON API trả 503 khi dependencies lỗi và 504 khi hết thời gian. Nội dung bị policy
chặn nhận response an toàn với status=blocked. Không có endpoint mint token công khai.
Browser nên gọi BFF của backend web; BFF ký JWT và chuyển stream. Không để browser
nắm các khóa ký. BFF/UI thuộc phần web, không được tạo thêm trong bản khung này.

## Code nằm ở đâu?

| File/package | Một trách nhiệm chính |
| --- | --- |
| src/main.py | Tạo app và khởi tạo/đóng dependencies |
| src/config.py | Đọc và kiểm tra cấu hình |
| src/auth.py | Kiểm tra JWT web và ký JWT chuyển sang MCP |
| src/api/dependencies.py | Đọc bearer từ HTTP, trả 401 khi không hợp lệ |
| src/schemas/chat.py | Kiểu dữ liệu request/response chat |
| src/api/chat.py | Endpoint JSON/SSE và ánh xạ lỗi HTTP |
| src/api/sse.py | Encode sự kiện, heartbeat, queue và hủy stream |
| src/api/service.py | Điều phối một lượt chat, độc lập HTTP |
| src/api/conversations.py | Lưu lịch sử và khóa hội thoại bằng SQLite |
| src/agents/factory.py | Chọn assistant theo role |
| src/agents/base_assistant/ | Interface, context và kết quả chung |
| src/agents/student_assistant/ | Khai báo graph, state và từng bước xử lý |
| src/services/llms/client.py | Gọi model qua HTTP |
| src/services/mcp/mcp.py | Kết nối MCP, kiểm tra allowlist và tham số tool |
| src/guardrails/ | Đánh giá an toàn đầu vào/đầu ra |
| src/schemas/auth.py | Kiểu dữ liệu người dùng đã xác thực |

Các đường dẫn `services/llms` và `services/mcp` theo tên bạn đã chỉnh.
Embeddings/reranker chưa được gắn vào runtime vì hiện tại dữ liệu học vụ được
tra cứu qua MCP. Khi bổ sung hỏi đáp tài liệu, triển khai chúng tại các thư mục
`services/embeddings` và `services/reranker` đang có.

Đọc code theo thứ tự `main.py` → `api/chat.py` → `api/service.py` → agent.
Khi cần hiểu chi tiết một việc, mở module phụ trách việc đó: JWT ở `auth.py`,
stream ở `api/sse.py`, lịch sử ở `api/conversations.py`. Không phải đọc model
client hoặc truy vấn database để hiểu cách một endpoint HTTP hoạt động.

Một lượt: xác thực → kiểm tra role/owner/lease → input guardrail → model ↔ MCP
trong ngân sách → output guardrail → commit history → answer/done. LLM không có
quyền đổi role. MCP kiểm tra token độc lập; readOnlyHint chỉ là metadata, quyền
đọc thật được bảo vệ bởi adapter và tài khoản database.

Guardrails dùng classifier riêng về prompt nhưng cùng endpoint model, output JSON
được Pydantic kiểm tra. Cho phép trò chuyện ngoài học tập an toàn. Khi classifier
không khả dụng, không phát bản nháp. Cần eval trên model thật để đánh giá jailbreak,
multi-turn và chất lượng; unit tests không chứng minh an toàn tuyệt đối.

History nằm riêng với dữ liệu học vụ, giữ tối đa 12 cặp gần nhất. Lease bằng transaction
hỗ trợ nhiều worker cùng filesystem. Commit từ lease cũ phải bị từ chối; release
idempotent. Storage có Protocol để thay PostgreSQL/Redis mà không sửa agent.
Semaphore theo process chưa phải rate limit phân tán. Chưa có retention/xóa history,
idempotency request hoặc manager write tools. Cancel coroutine không tự dừng thread
SQLite đã chạy; thuật toán có budget, DB thật cần statement timeout riêng.

## Kiểm thử

Với môi trường đã cài dev dependencies, chạy riêng hai suite:

```bash
cd backend
python -m pytest tests -q
cd ../mcp
python -m pytest tests -q
```

Trong phiên này, pytest/ruff được cài riêng vào `/tmp/hust-review-tools`, không
nâng cấp các gói vLLM. Ví dụ từ thư mục backend:

```bash
PYTHONPATH=/tmp/hust-review-tools PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/fil/miniconda3/envs/vllm/bin/python -m pytest tests -q \
  -p no:cacheprovider -p pytest_asyncio.plugin
```

Tests dùng model giả lập. Integration test chạy MCP process thật + SDK + SQLite demo,
không cần GPU/API key. Tài liệu tham khảo: [LangGraph](https://docs.langchain.com/oss/python/langgraph/graph-api),
[MCP SDK v1](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x),
[AnyIO timeout/cancellation](https://anyio.readthedocs.io/en/stable/cancellation.html).
