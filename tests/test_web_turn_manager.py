# tests/test_web_turn_manager.py
import asyncio
import json
import time

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


def test_replay_skips_corrupted_lines(tm):
    """events.jsonl 中存在坏行（崩溃半写）时，replay 跳过坏行返回其余事件。"""
    t = tm.create_turn("p1")
    tm.append_event("p1", t.turn_id, "message_delta", {"text": "a"})
    events_path = tm._root / "p1" / "turns" / t.turn_id / "events.jsonl"
    with events_path.open("a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    tm.append_event("p1", t.turn_id, "message_delta", {"text": "b"})
    events = tm.replay("p1", t.turn_id, after_seq=0)
    assert [e.seq for e in events] == [1, 2]


def test_recover_interrupted_skips_corrupted_meta(tm):
    """meta.json 损坏的回合目录不应让 recover_interrupted 崩溃；其余回合照常恢复。"""
    good = tm.create_turn("p1")
    bad_dir = tm._root / "p1" / "turns" / "badturn0000000000"
    bad_dir.mkdir(parents=True)
    (bad_dir / "meta.json").write_text("{not valid json", encoding="utf-8")
    # 审后补：合法 JSON 但非 dict（旧格式残留/外部写入）同样属坏 meta，
    # 不得让启动路径 recover 崩溃
    bad_dir2 = tm._root / "p1" / "turns" / "badturn0000000001"
    bad_dir2.mkdir(parents=True)
    (bad_dir2 / "meta.json").write_text("[1, 2]", encoding="utf-8")
    tm.recover_interrupted(["p1"])
    assert tm.get_turn("p1", good.turn_id).status == "interrupted"


def test_write_meta_leaves_no_temp_files(tm):
    """原子写后回合目录只含 meta.json / events.jsonl，无临时文件碎片；meta 始终可解析。"""
    t = tm.create_turn("p1")
    tm.finish("p1", t.turn_id, "completed")
    turn_dir = tm._root / "p1" / "turns" / t.turn_id
    assert sorted(p.name for p in turn_dir.iterdir()) == ["meta.json"]
    meta = json.loads((turn_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"


def test_atomic_write_json_fsyncs_before_replace(tmp_path, monkeypatch):
    """加固（审查 1.3）：os.replace 前先 fsync 落盘——rename 只保证名字原子、
    不保证数据落盘；掉电后目标文件不得是零字节空壳。"""
    import fieldwork_agent.web_host.turn_manager as tm_mod
    synced = []
    monkeypatch.setattr(tm_mod.os, "fsync", lambda fd: synced.append(fd))
    path = tmp_path / "meta.json"
    tm_mod._atomic_write_json(path, {"a": 1})
    assert len(synced) == 1  # replace 前恰好 fsync 一次
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert not list(tmp_path.glob("*.tmp"))  # 无残留临时文件


def test_recover_interrupted_leaves_terminal_turns_untouched(tm):
    """锚定（审查 1.4）：recover 只针对 running 回合——四种终态的磁盘 meta
    保持原样，不被误标 interrupted（误恢复会把已完结回合当崩溃残留）。"""
    statuses = ["completed", "failed", "cancelled", "interrupted"]
    for status in statuses:
        t = tm.create_turn("p1")
        tm.finish("p1", t.turn_id, status)
    tm.recover_interrupted(["p1"])
    turns_dir = tm._root / "p1" / "turns"
    on_disk = set()
    for d in turns_dir.iterdir():
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        on_disk.add(meta["status"])
    assert on_disk == set(statuses)


# --------------------------------------------------- A02：内存态回收
def test_prune_terminal_evicts_after_retention(tm):
    """终态回合按 TTL 退场：否则长期运行按回合数线性增长，只在删项目时清。"""
    from fieldwork_agent.web_host import turn_manager as tm_mod

    t = tm.create_turn("p1", idempotency_key="k1")
    tm.finish("p1", t.turn_id, "completed")
    assert tm.prune_terminal(now=time.time() + tm_mod.TERMINAL_RETENTION + 1) == 1
    assert tm.get_turn("p1", t.turn_id) is None
    assert tm.lookup_idempotent("p1", "k1") is None


def test_prune_terminal_keeps_running_turns(tm):
    t = tm.create_turn("p1")
    assert tm.prune_terminal(now=time.time() + 10**6) == 0
    assert tm.get_turn("p1", t.turn_id) is not None


def test_prune_terminal_keeps_fresh_terminal_turns(tm):
    """刚结束的不能马上清：SSE 重连与幂等重试还要靠它。"""
    t = tm.create_turn("p1")
    tm.finish("p1", t.turn_id, "completed")
    assert tm.prune_terminal() == 0
    assert tm.get_turn("p1", t.turn_id) is not None


def test_create_turn_amortizes_prune(tm):
    """不需要后台任务：新回合创建时顺手清一次过期项。"""
    from fieldwork_agent.web_host import turn_manager as tm_mod

    old = tm.create_turn("p1")
    tm.finish("p1", old.turn_id, "completed")
    old.finished_at = time.time() - tm_mod.TERMINAL_RETENTION - 1
    tm.create_turn("p1")
    assert tm.get_turn("p1", old.turn_id) is None


def test_unsubscribe_drops_empty_subscriber_entry(tm):
    """订阅表也不能留空壳 key，否则每回合泄一个空列表。"""
    t = tm.create_turn("p1")
    q = tm.subscribe("p1", t.turn_id)
    tm.unsubscribe("p1", t.turn_id, q)
    assert tm._subscribers.get(f"p1:{t.turn_id}") is None
