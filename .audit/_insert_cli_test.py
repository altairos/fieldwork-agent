# 一次性插入脚本（任务4）：向 tests/test_loop_orchestrator.py 追加 CLI 批截断测试
from pathlib import Path

path = Path("tests/test_loop_orchestrator.py")
text = path.read_text(encoding="utf-8")

anchor = """    assert r.stopped_by_limit is True
    assert len(r.tool_events) == 1, "总调用数上限=1 时只派发一次"


# ---------------------------------------------------------------- orchestrator 接线
"""

addition = """    assert r.stopped_by_limit is True
    assert len(r.tool_events) == 1, "总调用数上限=1 时只派发一次"


def test_tool_budget_truncates_batch_midway():
    \"\"\"F07：一批 4 调用只剩 2 额度 → 只执行前 2，后 2 回填 budget_exceeded。\"\"\"
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [
            {"id": f"c{i}", "name": "search_pois", "arguments": {"query": f"q{i}"}}
            for i in range(4)]},
        {"content": "预算内结论", "tool_calls": None},
    ])
    calls = []

    def dispatch(name, args):
        calls.append(name)
        return Envelope(ok=True, data={"n": len(calls)})

    result = run_tool_loop(llm, [], dispatch, [], max_tool_calls=2)
    assert calls == ["search_pois", "search_pois"]      # 只执行前 2
    skipped = [e for e in result.tool_events if not e["ok"]]
    assert len(skipped) == 2
    assert all(e["error"].code == "budget_exceeded" for e in skipped)
    assert result.stopped_by_limit is True  # 触顶后仍取最终回答，脚本第 2 帧即收敛帧


# ---------------------------------------------------------------- orchestrator 接线
"""

assert text.count(anchor) == 1, f"anchor count = {text.count(anchor)}"
path.write_text(text.replace(anchor, addition), encoding="utf-8")
print("inserted")
