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
cp backend/config.example.yaml backend/config.yaml
```

Tạo hai secret ngẫu nhiên KHÁC NHAU bằng lệnh sau (chạy hai lần):

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

- `ASSISTANT_AUTH_SECRET`: backend web và assistant API cùng biết.
- `ASSISTANT_MCP_SECRET`: assistant API và Academic MCP cùng biết.

Mỗi secret ít nhất 32 ký tự. Thay các placeholder trong `.env.example`; không đưa
secret/token vào Git, browser hoặc prompt. `backend/config.yaml` chứa cấu hình local;
`config.example.yaml` là bản mẫu có comment. API key `sk-1234` là giá trị mẫu cho
model server local; có thể ghi đè bằng `ASSISTANT_LLM__API_KEY` trong `.env`.
Environment có ưu tiên cao hơn `.env`, rồi đến YAML và mặc định.
Ví dụ `ASSISTANT_LLM__BASE_URL` ghi đè `llm.base_url`, còn `ASSISTANT_APP__PORT`
ghi đè `app.port`. Các tên cũ `api_base_url`, `model_name`, `mcp_url` đã được thay
bằng `llm.base_url`, `llm.default_model`, `mcp.url`.

| Nhóm YAML | Ý nghĩa |
| --- | --- |
| `app` | Host/port, reload, log level, timeout và số lượt chạy đồng thời |
| `ui` | Địa chỉ web demo, đọc bởi ui/scripts/serve.mjs; backend không tự khởi chạy UI |
| `llm` | Kết nối vLLM, model mặc định và tham số sinh nội dung |
| `embeddings` | Cấu hình dự kiến; client chưa triển khai nên chỉ nhận `enabled: false` |
| `mcp` | URL và timeout cho session/tool calls |
| `agents` | Giới hạn vòng gọi model/tools |
| `short_memory` | Bật/tắt việc đưa lịch sử vào model; đường dẫn và cửa sổ lưu hội thoại |
| `long_memory` | Chưa triển khai; chỉ nhận `enabled: false` |

`short_memory.enabled: false` không xóa transcript: SQLite vẫn giữ owner/lease
và lưu hội thoại, nhưng model chỉ nhận system prompt và câu hỏi hiện tại. Đổi
thành `true` để model nhận lịch sử các lượt trước. Không có cấu hình
`legal_assistant`, PostgreSQL, vector store, rewrite hoặc HyDE hoạt động trong
project học vụ hiện tại.

Chạy MCP theo [hướng dẫn](../mcp/README.md). Trong terminal khác, từ thư mục gốc:

```bash
cd backend
python -m src.main
```

Lệnh trên đọc `app.host`, `app.port`, `app.reload`, `app.log_level` từ YAML;
mặc định chạy ở cổng 8023. Khi chạy trực tiếp Uvicorn CLI, tham số host/port của
CLI được dùng thay cho nhóm `app` trong YAML.

Qwen/vLLM cần chạy riêng theo `llm.base_url`, hỗ trợ tool calling, streaming
Chat Completions và JSON mode.
Tên model mặc định là `qwen3-8b-fp8`; chỉnh cho khớp server thực tế. Không tự tải
model khi import hoặc khởi động app. Nếu server không nhận `chat_template_kwargs`,
đặt `ASSISTANT_LLM__SEND_CHAT_TEMPLATE_KWARGS=false`.

`GET /health` là liveness. Swagger ở `/docs`. Hai service đều dùng package `src`,
vì vậy phải chạy trong đúng thư mục và kiểm thử bằng hai process riêng.

## API và danh tính

Backend web đăng nhập người dùng, lấy role từ account rồi ký JWT HS256 với claims.
Chỉ có hai role `student` và `admin`; tài khoản quản lý dùng `admin`.
Role khác bị từ chối ngay khi xác thực, kể cả JWT được ký đúng.

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
curl -N http://127.0.0.1:8023/api/v1/chat/stream \
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
| 401 | Token thiếu/sai/hết hạn/sai issuer hoặc audience, hoặc role không hợp lệ |
| 403 | Sai quyền hoặc không sở hữu hội thoại |
| 409 | Hội thoại đang xử lý lượt khác |
| 422 | Body sai schema, thừa trường, nội dung rỗng hoặc quá dài |
| 501 | Agent cho admin chưa triển khai |

JSON API trả 503 khi dependencies lỗi và 504 khi hết thời gian. Nội dung bị policy
chặn nhận response an toàn với status=blocked. Không có endpoint mint token công khai.
Browser nên gọi BFF của backend web; BFF ký JWT và chuyển stream. Không để browser
nắm các khóa ký. Bản mô phỏng web/BFF hiện có trong [ui/](../ui/README.md),
đọc tài khoản từ `accounts.yaml` ở gốc repo; không thêm API login vào assistant.

## Code nằm ở đâu?

| File/package | Một trách nhiệm chính |
| --- | --- |
| src/main.py | Tạo app và khởi tạo/đóng dependencies |
| src/config.py | Đọc và kiểm tra cấu hình |
| src/auth.py | Kiểm tra JWT web và ký JWT chuyển sang MCP |
| src/api/dependencies.py | Đọc bearer từ HTTP, trả 401 khi không hợp lệ |
| src/schemas/chat.py | Kiểu dữ liệu request/response chat |
| src/schemas/agent.py | Trạng thái và kết quả chung của agent |
| src/schemas/events.py | Kiểu dữ liệu progress, status event và result event |
| src/api/chat.py | Endpoint JSON/SSE và ánh xạ lỗi HTTP |
| src/api/sse.py | Format sự kiện SSE và gửi heartbeat |
| src/api/service.py | Điều phối chat, lưu kết quả và cung cấp run/astream |
| src/api/conversations.py | Lưu lịch sử và khóa hội thoại bằng SQLite |
| src/agents/factory.py | Chọn assistant theo role |
| src/agents/base_assistant/agent.py | Vòng đời run/astream chung và điểm mở rộng graph |
| src/agents/base_assistant/context.py | Danh tính, request ID và session ID của lượt gọi |
| src/agents/base_assistant/state.py | State chung cho các graph |
| src/agents/base_assistant/events.py | Queue có giới hạn và vòng đời tác vụ streaming |
| src/agents/student_assistant/ | Khai báo graph, state và từng bước xử lý |
| src/services/llms/client.py | Gọi model qua HTTP |
| src/services/mcp/mcp.py | Kết nối MCP, kiểm tra allowlist và tham số tool |
| src/guardrails/ | Đánh giá an toàn đầu vào/đầu ra |
| src/schemas/auth.py | Kiểu dữ liệu người dùng đã xác thực |
| src/schemas/guardrails.py | Kiểu dữ liệu kết quả kiểm tra an toàn |

Các đường dẫn `services/llms` và `services/mcp` theo tên bạn đã chỉnh.
Embeddings/reranker chưa được gắn vào runtime vì hiện tại dữ liệu học vụ được
tra cứu qua MCP. Khi bổ sung hỏi đáp tài liệu, triển khai chúng tại các thư mục
`services/embeddings` và `services/reranker` đang có.

Đọc code theo thứ tự `main.py` → `api/chat.py` → `api/service.py` → agent.
Khi cần hiểu chi tiết một việc, mở module phụ trách việc đó: JWT ở `auth.py`,
stream ở `agents/base_assistant/events.py`, định dạng SSE ở `api/sse.py`, lịch sử
ở `api/conversations.py`. Không phải đọc model
client hoặc truy vấn database để hiểu cách một endpoint HTTP hoạt động.

Một lượt: xác thực → kiểm tra role/owner/lease → input guardrail → model ↔ MCP
trong ngân sách → output guardrail → commit history → answer/done. LLM không có
quyền đổi role. MCP kiểm tra token độc lập; readOnlyHint chỉ là metadata, quyền
đọc thật được bảo vệ bởi adapter và tài khoản database.

`BaseAssistant` đóng gói vòng đời trong `run()` được đánh dấu `@final`: kiểm tra
đầu vào → ghép system prompt, bản sao lịch sử và tin nhắn mới → mở MCP tools theo
`context.current_user` → dựng graph → chạy với giới hạn recursion → đóng tools →
kiểm tra đầu ra → trả `AgentResult`. Nhánh hết ngân sách cũng qua kiểm tra đầu ra.
Constructor chung nhận dependencies và giữ ở `_llm`, `_tools_factory`, `_guardrails`,
`_settings`; subclass không phải lặp lại khởi tạo hay `run()`.

Khi thêm assistant, kế thừa `BaseAssistant` rồi triển khai property `system_prompt`
và `_build_graph(tools, context, emit)`. `_initial_state(messages)` có mặc định gồm
`messages`, `response`, `status`; chỉ override nếu graph cần thêm dữ liệu.
Các trường này được khai báo bằng `AgentState`, kế thừa `MessagesState` của
LangGraph; `StudentState` kế thừa và bổ sung `draft`, `tool_rounds`, `tool_calls`.
Mỗi lượt tạo state riêng, không chia sẻ messages hoặc bộ đếm giữa các request.
`StudentAssistant` cung cấp prompt,
dựng graph và khởi tạo các trường bổ sung. `StudentNodes.output` chỉ dựng
response/status; việc gọi guardrails thuộc base. Agent cho admin vẫn chưa triển khai.

Trong graph, `messages` chứa các đối tượng LangChain, không chứa dict thô.
`BaseMessage` là kiểu cha; code tạo `SystemMessage` cho chỉ dẫn, `HumanMessage` cho
người dùng, `AIMessage` cho model và `ToolMessage` cho kết quả tool. Base chuyển
history lưu trữ sang các kiểu này, rồi thêm system prompt và câu hỏi mới.
Reducer `add_messages` của `MessagesState` ghép các cập nhật vào lịch sử: node chỉ
trả message mới, ví dụ `{"messages": [ai_message]}`, không trả lại toàn bộ history.
Nhờ đó chu trình model → tool → model không nhân đôi tin nhắn.

Biểu diễn message dạng JSON/dict chỉ xuất hiện ở biên HTTP và lưu trữ SQLite;
LLM client chuyển đổi khi gửi/nhận API vLLM. `AIMessage.tool_calls` chứa tham số
đã parse thành dict; tham số hỏng nằm trong `invalid_tool_calls` và được trả lỗi
qua `ToolMessage` có đúng `tool_call_id`, không gọi MCP.

Model sinh câu trả lời bằng `LLMClient.astream()`, nhận các `AIMessageChunk` nội bộ.
`BaseAssistant._generate()` ghép chunk bằng phép cộng, rồi chuyển thành một
`AIMessage` hoàn chỉnh cho graph. Tham số tool được parse lại bằng JSON đầy đủ sau
khi ghép xong; không thực thi tool dựa trên JSON còn dang dở trong chunk.
`LLMClient.complete()` vẫn dùng JSON không streaming cho classifier guardrails.
API tiếp tục chỉ phát tiến độ và câu trả lời hoàn chỉnh đã kiểm tra, không chuyển
chunk hoặc token thô ra UI.

`run()` trả `AgentResult`; `astream()` trả các sự kiện có kiểu, gồm progress
và result. `AgentProgress` chỉ chấp nhận những stage do code định nghĩa; mỗi event
có request ID, session ID và sequence. Event không mang messages, bản nháp hay
tool payload. Result của agent luôn đã qua kiểm tra đầu ra.

`BaseAssistant.astream()` và `ChatService.astream()` dùng chung runtime
`AgentEvents.stream(run_callback)` trong `base_assistant/events.py`. Runtime tạo
một queue có giới hạn cho mỗi stream và hủy/đợi tác vụ khi consumer đóng stream.
Service bọc `self.run()` của chính nó, trong đó agent chạy và kết quả được commit;
service không bọc thêm stream của agent. Vì vậy luồng API chỉ có một queue,
và result chỉ xuất hiện sau khi lưu hội thoại thành công. Nếu cần gọi riêng agent
qua CLI hoặc test, có thể dùng `BaseAssistant.astream()` trực tiếp.

SSE adapter nhận event từ service, format thành `status`/`answer`, gửi heartbeat
và kết thúc bằng `done`; adapter không giữ queue riêng. Lỗi thực thi vẫn là
exception nội bộ để JSON API ánh xạ đúng HTTP status. Với SSE, adapter chuyển lỗi
thành `error` an toàn rồi `done`, không phát nội dung exception hoặc answer khi
commit thất bại. Streaming hiện tại phát tiến độ và câu trả lời hoàn chỉnh đã
kiểm tra, không stream token chưa qua guardrails.

Không lưu tools, context hoặc state của một lượt lên `self`: cùng assistant có thể
phục vụ nhiều lượt đồng thời. Các tên bắt đầu bằng `_` và `@final` thể hiện quy ước
kế thừa/kiểm tra kiểu của Python, không ngăn ghi đè ở runtime và không phải cơ chế
phân quyền. Factory chọn agent theo role; MCP vẫn xác thực quyền độc lập.

Guardrails dùng classifier riêng về prompt nhưng cùng endpoint model, output JSON
được Pydantic kiểm tra. Cho phép trò chuyện ngoài học tập an toàn. Khi classifier
không khả dụng, không phát bản nháp. Cần eval trên model thật để đánh giá jailbreak,
multi-turn và chất lượng; unit tests không chứng minh an toàn tuyệt đối.

History nằm riêng với dữ liệu học vụ, cửa sổ lưu do `short_memory.max_messages`
quyết định (mặc định 24 messages, khoảng 12 cặp). Lease bằng transaction
hỗ trợ nhiều worker cùng filesystem. Commit từ lease cũ phải bị từ chối; release
idempotent. Storage có Protocol để thay PostgreSQL/Redis mà không sửa agent.
Semaphore theo process chưa phải rate limit phân tán. Chưa có retention/xóa history,
idempotency request hoặc tools ghi dữ liệu cho admin. Cancel coroutine không tự dừng thread
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
