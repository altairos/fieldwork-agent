# -*- coding: utf-8 -*-
"""F03 步骤 3.1：改写 tests/test_web_agent_loop.py 的时窗回写测试（TDD 红阶段）。

基于唯一锚点做精确替换：test_analyze_window_writes_time_window_to_research_context
→ test_analyze_window_writes_candidate_windows_not_constraint（计划 590-613 行逐字）。
函数体后的快照落盘断言（696-698 行）保持不动。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "test_web_agent_loop.py"

OLD = '''def test_analyze_window_writes_time_window_to_research_context(env):
    """spec §5.3：analyze 成功后宿主把推荐时窗确定性回写 research_context.time_window。

    FakeFlyAI 采样恒 cheapest=100 → 手算首窗 [07-01, 07-03]：
    est = 100×2 航 + 100×2 晚 + 150×3 餐 = 850 → reasons[0] 含「估算 ¥850」。
    """
    repo, tm = env
    llm = WindowLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "analyze_fieldwork_window",
                                         "arguments": dict(_ANALYZE_ARGS)}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好"},
         {"content": "好", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ], chat_content='[{"name": "火把节", "approx_start": "2026-07-05",'
                    ' "approx_end": "2026-07-06", "approx_dates": "农历六月廿四",'
                    ' "relevance": "r"}]')
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    tw = repo.get_research_context("p1")["time_window"]
    assert tw.startswith("2026-07-01 ~ 2026-07-03（推荐时窗：")
    assert "估算 ¥850" in tw'''

NEW = '''def test_analyze_window_writes_candidate_windows_not_constraint(env):
    """F03：推荐时窗写 candidate_windows，time_window（用户约束）保持为空。"""
    repo, tm = env
    llm = WindowLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "analyze_fieldwork_window",
                                         "arguments": dict(_ANALYZE_ARGS)}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好"},
         {"content": "好", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ], chat_content='[{"name": "火把节", "approx_start": "2026-07-05",'
                    ' "approx_end": "2026-07-06", "approx_dates": "农历六月廿四",'
                    ' "relevance": "r"}]')
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    ctx = repo.get_research_context("p1")
    assert ctx.get("time_window", "") == ""
    wins = ctx["candidate_windows"]
    assert wins and wins[0]["start"] == "2026-07-01" and wins[0]["end"] == "2026-07-03"
    assert "估算 ¥850" in wins[0]["reason"]'''

data = TARGET.read_bytes()
text = data.decode("utf-8")
crlf = "\r\n" in text
if crlf:
    text = text.replace("\r\n", "\n")
n_old = text.count(OLD)
assert n_old == 1, f"old block found {n_old} times (expect 1)"
assert NEW not in text, "new block already present"
text = text.replace(OLD, NEW, 1)
assert text.count(NEW) == 1
if crlf:
    text = text.replace("\n", "\r\n")
TARGET.write_bytes(text.encode("utf-8"))
print(f"OK: rewritten, crlf={crlf}")
