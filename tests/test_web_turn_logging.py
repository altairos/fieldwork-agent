"""A06：回合级最小可观测性——turn_start/turn_end/crash/tool_call 日志。"""

import asyncio
import logging

from fieldwork_agent.kernel.models import Envelope
from fieldwork_agent.web_host.agent import loop
from fieldwork_agent.web_host.turn_manager import TurnManager


def _tm_with_turn(tmp_path, pid="p1"):
    tm = TurnManager(root=tmp_path / "projects")
    turn, _ = tm.try_create_turn(pid)
    return tm, turn


def test_run_turn_logs_start_and_end(tmp_path, monkeypatch, caplog):
    tm, turn = _tm_with_turn(tmp_path)

    async def fake_impl(repo, pid, tid, text, tm_, llm, flyai, **kw):
        tm_.finish(pid, tid, "completed")

    monkeypatch.setattr(loop, "_run_turn_impl", fake_impl)
    with caplog.at_level(logging.INFO):
        asyncio.run(loop.run_turn(
            None, "p1", turn.turn_id, "hi", tm, None, None,
            owner="anon:supersecret-token"))
    text = caplog.text
    assert "turn_start" in text and turn.turn_id in text
    assert "turn_end" in text and "status=completed" in text
    assert "duration_ms=" in text
    assert "supersecret-token" not in text      # owner 只留命名空间，不落令牌


def test_run_turn_logs_crash_and_failed_status(tmp_path, monkeypatch, caplog):
    tm, turn = _tm_with_turn(tmp_path)

    async def boom(*_a, **_kw):
        raise RuntimeError("磁盘满")

    monkeypatch.setattr(loop, "_run_turn_impl", boom)
    with caplog.at_level(logging.INFO):
        asyncio.run(loop.run_turn(None, "p1", turn.turn_id, "hi", tm, None, None))
    assert "turn_crashed" in caplog.text and "磁盘满" in caplog.text
    assert "status=failed" in caplog.text
    assert tm.get_turn("p1", turn.turn_id).status == "failed"


def test_exec_tool_logs_name_and_outcome(caplog):
    def dispatch(name, args):
        return Envelope(ok=True)

    with caplog.at_level(logging.INFO):
        asyncio.run(loop._exec_tool(dispatch, "search_hotel", {}, "c1", {}))
    assert "tool_call" in caplog.text
    assert "name=search_hotel" in caplog.text and "ok=True" in caplog.text


def test_create_app_configures_app_logger(tmp_path, monkeypatch, fake_config):
    """A06：应用 logger 挂了 handler（否则 INFO 在 uvicorn 下会被丢弃）；重复建 app 不叠加。"""
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host import app as app_mod

    logger = logging.getLogger("fieldwork_agent")
    before_handlers = list(logger.handlers)
    before_level = logger.level
    monkeypatch.setattr(app_mod, "_LOGGING_CONFIGURED", False)
    monkeypatch.setenv("FA_LOG_LEVEL", "DEBUG")
    try:
        repo = JsonRepository(projects_dir=tmp_path / "projects")
        app_mod.create_app(config=fake_config, repo=repo)
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) == len(before_handlers) + 1
        app_mod.create_app(config=fake_config, repo=repo)
        assert len(logger.handlers) == len(before_handlers) + 1   # 幂等
    finally:
        logger.handlers[:] = before_handlers
        logger.setLevel(before_level)
