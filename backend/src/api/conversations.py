"""Lịch sử riêng của chatbot; không ghi vào database học vụ.

Mỗi lượt giữ một lease SQLite. Cặp user/assistant chỉ được commit khi lease
còn hiệu lực, để request cũ không ghi đè kết quả của worker vừa tiếp quản.
"""

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import anyio

from src.schemas.auth import CurrentUser

History = list[dict[str, str]]


class ConversationForbidden(Exception):
    """Session thuộc tài khoản hoặc role khác."""


class ConversationBusy(Exception):
    """Session đang được một request khác xử lý."""


class ConversationLeaseLost(Exception):
    """Request đã hết quyền lưu kết quả; không được gửi answer cho client."""


class ConversationStore(Protocol):
    """Giao diện nhỏ để có thể thay SQLite bằng Redis/PostgreSQL sau này."""

    async def initialize(self) -> None: ...

    async def acquire(
        self, session_id: UUID, user: CurrentUser, timeout: float
    ) -> tuple[str, History]: ...

    async def commit(self, session_id: UUID, lease: str, history: History) -> None: ...

    async def release(self, session_id: UUID, lease: str) -> None: ...


class Conversations:
    def __init__(self, path: Path):
        self.path = Path(path)

    async def initialize(self) -> None:
        # SQLite chạy trên worker thread; không chặn event loop của FastAPI.
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, role TEXT NOT NULL,
                history TEXT NOT NULL DEFAULT '[]', lease TEXT, busy_until REAL DEFAULT 0
            )""")
            db.commit()

    async def acquire(
        self, session_id: UUID, user: CurrentUser, timeout: float
    ) -> tuple[str, History]:
        worker = asyncio.create_task(
            asyncio.to_thread(self._acquire, str(session_id), user, timeout)
        )
        try:
            # Hủy coroutine không dừng được SQLite thread. Giữ worker để thu hồi
            # lease nếu thread cấp lease sau lúc HTTP client đã ngắt kết nối.
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                try:
                    lease, _ = await asyncio.shield(worker)
                except Exception:
                    pass
                else:
                    await asyncio.shield(self.release(session_id, lease))
            raise

    def _acquire(self, session_id: str, user: CurrentUser, timeout: float) -> tuple[str, History]:
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT owner, role, history, busy_until FROM conversations WHERE id=?",
                    (session_id,),
                ).fetchone()
                if row and (row[0] != user.user_id or row[1] != user.role):
                    raise ConversationForbidden()
                if row and row[3] > time.time():
                    raise ConversationBusy()
                lease = str(uuid4())
                if not row:
                    db.execute(
                        "INSERT INTO conversations(id,owner,role) VALUES(?,?,?)",
                        (session_id, user.user_id, user.role),
                    )
                db.execute(
                    "UPDATE conversations SET lease=?,busy_until=? WHERE id=?",
                    (lease, time.time() + timeout + 30, session_id),
                )
                history = json.loads(row[2]) if row else []
                db.commit()
                return lease, history
            except BaseException:
                db.rollback()
                raise

    async def commit(self, session_id: UUID, lease: str, history: History) -> None:
        await asyncio.to_thread(self._commit, str(session_id), lease, history)

    def _commit(self, session_id: str, lease: str, history: History) -> None:
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            # BEGIN IMMEDIATE makes lease validation and history update atomic.
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute(
                "UPDATE conversations SET history=? WHERE id=? AND lease=? AND busy_until>?",
                (json.dumps(history[-24:], ensure_ascii=False), session_id, lease, time.time()),
            )
            if updated.rowcount != 1:
                db.rollback()
                raise ConversationLeaseLost()
            db.commit()

    async def release(self, session_id: UUID, lease: str) -> None:
        await asyncio.to_thread(self._release, str(session_id), lease)

    def _release(self, session_id: str, lease: str) -> None:
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            # Idempotent, và lease cũ không thể mở khóa lượt mới của cùng session.
            db.execute(
                "UPDATE conversations SET lease=NULL,busy_until=0 WHERE id=? AND lease=?",
                (session_id, lease),
            )
            db.commit()
