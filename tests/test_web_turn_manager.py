# tests/test_web_turn_manager.py
import asyncio

import pytest
from fieldwork_agent.web_host.turn_manager import TurnManager


@pytest.fixture
def tm(tmp_path):
    return TurnManager(root=tmp_path / "projects")


def test_create_turn_and_idempotency(tm):
    t1 = tm.create_turn("p1", idempotency_key="key-a")
    t2 = tm.create_turn("p1", idempotency_key="key-a")
    assert t1.turn_id == t2.turn_id
    t3 = tm.create_turn("p1", idempotency_key="key-b")
    assert t3.turn_id != t1.turn_id


def test_append_event_increments_seq(tm):
    t = tm.create_turn("p1")
    e1 = tm.append_event("p1", t.turn_id, "message_delta", {"text": "a"})
    e2 = tm.append_event("p1", t.turn_id, "message_delta", {"text": "b"})
    assert e1.seq == 1 and e2.seq == 2


def test_replay_after_seq(tm):
    t = tm.create_turn("p1")
    for i in range(5):
        tm.append_event("p1", t.turn_id, "message_delta", {"text": str(i)})
    events = tm.replay("p1", t.turn_id, after_seq=3)
    assert [e.seq for e in events] == [4, 5]


def test_finish_sets_terminal(tm):
    t = tm.create_turn("p1")
    tm.finish("p1", t.turn_id, "completed")
    assert tm.get_turn("p1", t.turn_id).status == "completed"


def test_cancel(tm):
    t = tm.create_turn("p1")
    assert tm.cancel("p1", t.turn_id) is True
    assert t.cancel_event.is_set() and t.status == "cancelled"


def test_write_lock_serializes(tm):
    async def _run():
        order = []
        async with tm.acquire_write_lock("p1"):
            order.append("a")
            await asyncio.sleep(0.01)
        async with tm.acquire_write_lock("p1"):
            order.append("b")
        return order
    assert asyncio.run(_run()) == ["a", "b"]


def test_subscribe_receives_events(tm):
    async def _run():
        t = tm.create_turn("p1")
        q = tm.subscribe("p1", t.turn_id)
        tm.append_event("p1", t.turn_id, "message_delta", {"text": "hi"})
        tm.finish("p1", t.turn_id, "completed")
        got = await asyncio.wait_for(q.get(), timeout=1)
        assert got.seq == 1
        sentinel = await asyncio.wait_for(q.get(), timeout=1)
        assert sentinel is None
    asyncio.run(_run())


def test_recover_interrupted(tm):
    t = tm.create_turn("p1")
    tm.recover_interrupted(["p1"])
    assert tm.get_turn("p1", t.turn_id).status == "interrupted"


def test_is_writing(tm):
    async def _run():
        assert tm.is_writing("p1") is False
        async with tm.acquire_write_lock("p1"):
            assert tm.is_writing("p1") is True
    asyncio.run(_run())
