# tests/test_web_turn_terminal_guard.py
"""A01：回合必须收敛到终态——异常逃逸不得把项目永久卡在 running。

`_event_stream` 以 status 判终态、`retry_turn` 以 status 判可重试，因此
"停在 running"不是"少一个事件"，而是：SSE 只发心跳、流永不关闭，该回合
永久 409 不可重试，只能靠进程重启时的 recover_interrupted 恢复。

覆盖的是主循环与轮末持久化之外的两段——续流修复/安全终检（原 445–627）
与终态写入（原 671–684）：它们此前不在任何 try 里。
"""
import asyncio

import pytest
from fieldwork_agent.kernel.models import Envelope, ItemPage
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.turn_manager import TurnManager


class FakeLLM:
    """脚本化流式 LLM：chat_stream 按脚本 yield。"""

    def __init__(self, script):
        self.script = list(script)

    def chat_stream(self, messages, **kw):
        return iter(self.script.pop(0))


class FakeFlyAI:
    """本用例不走工具通道，但宿主会包 RecordingClient，故仍需可探测的属性。"""

    def __getattr__(self, name):
        def _stub(**kw):
            return Envelope(ok=True, data=ItemPage(items=[], total=0))
        return _stub


_SIMPLE_REPLY = [[
    {"delta": "你好"},
    {"content": "你好", "tool_calls": None, "finish_reason": "stop", "usage": {}},
]]


@pytest.fixture
def env(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    return repo, TurnManager(root=tmp_path / "projects")


def _run_quietly(coro):
    """后台任务语义：返回逃逸的异常（None 表示未逃逸）。"""
    try:
        asyncio.run(coro)
    except Exception as exc:      # noqa: BLE001 —— 正是被断言的行为
        return exc
    return None


def _explode(_text):
    raise RuntimeError("终检炸了")


def test_post_loop_error_does_not_escape_run_turn(env, monkeypatch):
    """异常必须被收敛，不能抛给 Starlette 的 BackgroundTasks（那里没人接）。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    import fieldwork_agent.web_host.agent.loop as loop_mod

    monkeypatch.setattr(loop_mod, "check_model_output", _explode)
    err = _run_quietly(loop_mod.run_turn(
        repo, "p1", turn.turn_id, "hi", tm, FakeLLM(_SIMPLE_REPLY), FakeFlyAI()))

    assert err is None, f"run_turn 不应把异常抛给后台任务：{err!r}"


def test_post_loop_error_marks_turn_failed(env, monkeypatch):
    repo, tm = env
    turn = tm.create_turn("p1")
    import fieldwork_agent.web_host.agent.loop as loop_mod

    monkeypatch.setattr(loop_mod, "check_model_output", _explode)
    _run_quietly(loop_mod.run_turn(
        repo, "p1", turn.turn_id, "hi", tm, FakeLLM(_SIMPLE_REPLY), FakeFlyAI()))

    assert tm.get_turn("p1", turn.turn_id).status == "failed"


def test_post_loop_error_emits_error_event_and_no_turn_done(env, monkeypatch):
    """客户端靠 turn_done 判成功；失败时绝不能留下它。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    import fieldwork_agent.web_host.agent.loop as loop_mod

    monkeypatch.setattr(loop_mod, "check_model_output", _explode)
    _run_quietly(loop_mod.run_turn(
        repo, "p1", turn.turn_id, "hi", tm, FakeLLM(_SIMPLE_REPLY), FakeFlyAI()))

    events = [e.event for e in tm.replay("p1", turn.turn_id, after_seq=0)]
    assert "error" in events
    assert "turn_done" not in events


def test_terminal_write_failure_still_lands_on_failed(env, monkeypatch):
    """终态写入自身抛错（磁盘满/权限）也要收敛——这是另一个未受保护的位点。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    real_append = tm.append_event

    def flaky(pid, tid, event, data):
        if event == "turn_done":
            raise OSError("磁盘满")
        return real_append(pid, tid, event, data)

    monkeypatch.setattr(tm, "append_event", flaky)
    import fieldwork_agent.web_host.agent.loop as loop_mod

    err = _run_quietly(loop_mod.run_turn(
        repo, "p1", turn.turn_id, "hi", tm, FakeLLM(_SIMPLE_REPLY), FakeFlyAI()))

    assert err is None
    assert tm.get_turn("p1", turn.turn_id).status == "failed"


def test_terminal_state_is_not_overwritten_by_guard(env, monkeypatch):
    """已取消/已终态的回合不得被兜底改写成 failed。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    import fieldwork_agent.web_host.agent.loop as loop_mod

    tm.cancel("p1", turn.turn_id)          # 先置终态，模拟 cancel 抢先
    monkeypatch.setattr(loop_mod, "check_model_output", _explode)
    _run_quietly(loop_mod.run_turn(
        repo, "p1", turn.turn_id, "hi", tm, FakeLLM(_SIMPLE_REPLY), FakeFlyAI()))

    assert tm.get_turn("p1", turn.turn_id).status == "cancelled"
