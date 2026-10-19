import asyncio
from uuid import uuid4

import pytest

from src.api.conversations import ConversationBusy, ConversationForbidden, Conversations
from src.schemas.auth import CurrentUser


async def test_history_owner_lock_and_restart_persistence(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = Conversations(path)
    await store.initialize()
    student = CurrentUser(user_id="one", role="student")
    session = uuid4()
    outcomes = await asyncio.gather(
        store.acquire(session, student, 10),
        store.acquire(session, student, 10),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, ConversationBusy) for outcome in outcomes) == 1
    lease, history = next(outcome for outcome in outcomes if isinstance(outcome, tuple))
    assert history == []
    await store.commit(session, lease, [{"role": "user", "content": "hello"}])
    await store.release(session, lease)
    restarted = Conversations(path)
    lease, history = await restarted.acquire(session, student, 10)
    assert history == [{"role": "user", "content": "hello"}]
    await restarted.release(session, lease)
    with pytest.raises(ConversationForbidden):
        await restarted.acquire(session, CurrentUser(user_id="two", role="student"), 10)


async def test_expired_or_superseded_lease_cannot_commit_or_unlock_new_turn(tmp_path):
    import sqlite3

    from src.api.conversations import ConversationLeaseLost

    path = tmp_path / "history.sqlite3"
    store = Conversations(path)
    await store.initialize()
    user = CurrentUser(user_id="one", role="student")
    session = uuid4()
    old, _ = await store.acquire(session, user, 10)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE conversations SET busy_until=0 WHERE id=?", (str(session),))
    with pytest.raises(ConversationLeaseLost):
        await store.commit(session, old, [{"role": "assistant", "content": "expired"}])
    current, history = await store.acquire(session, user, 10)
    assert history == []
    with pytest.raises(ConversationLeaseLost):
        await store.commit(session, old, [{"role": "assistant", "content": "stale"}])
    await store.release(session, old)
    with pytest.raises(ConversationBusy):
        await store.acquire(session, user, 10)
    await store.commit(session, current, [{"role": "assistant", "content": "current"}])
    await store.release(session, current)
    lease, history = await store.acquire(session, user, 10)
    assert history == [{"role": "assistant", "content": "current"}]
    await store.release(session, lease)


async def test_cancellation_during_acquire_releases_eventually_created_lease(tmp_path):
    import threading

    entered, unblock = threading.Event(), threading.Event()

    class SlowAcquire(Conversations):
        def _acquire(self, *args):
            entered.set()
            assert unblock.wait(timeout=5)
            return super()._acquire(*args)

    store = SlowAcquire(tmp_path / "history.sqlite3")
    await store.initialize()
    user = CurrentUser(user_id="one", role="student")
    session = uuid4()
    task = asyncio.create_task(store.acquire(session, user, 10))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    # Worker thread tiếp tục sau cancellation và tạo lease; phải thu hồi lease ấy.
    unblock.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    lease, history = await store.acquire(session, user, 10)
    assert history == []
    await store.release(session, lease)


async def test_history_has_a_bounded_context_window(tmp_path):
    store = Conversations(tmp_path / "history.sqlite3")
    await store.initialize()
    user = CurrentUser(user_id="one", role="student")
    session = uuid4()
    lease, _ = await store.acquire(session, user, 10)
    history = [{"role": "user", "content": str(index)} for index in range(40)]
    await store.commit(session, lease, history)
    await store.release(session, lease)
    lease, remembered = await store.acquire(session, user, 10)
    assert remembered == history[-24:]
    await store.release(session, lease)
