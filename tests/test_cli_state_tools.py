"""CLI orchestrator 接入 update_research_context（F02）。"""
from fieldwork_agent.core.orchestrator import FieldAgent


class ScriptedLLM:
    """按脚本顺序返回；记录全部 chat 入参。"""

    def __init__(self, script):
        self.script = list(script)
        self.seen: list[tuple[list, dict]] = []

    def chat(self, messages, **kw):
        self.seen.append((messages, kw))
        return self.script.pop(0)


def test_cli_update_research_context_persists(fake_config):
    """F02：CLI 会话内模型调用 update_research_context → 落盘 ResearchContext。"""
    agent = FieldAgent(fake_config)
    agent.create_project()
    agent.llm = ScriptedLLM([
        {"content": "", "tool_calls": [
            {"id": "c1", "name": "update_research_context",
             "arguments": {"patch": {"topic": "宗族与乡村治理"},
                           "reason": "用户确认主题"}}]},
        {"content": "已记下主题。", "tool_calls": None},
    ])
    out = agent.chat("研究主题是宗族与乡村治理")
    assert out["reply"] == "已记下主题。"
    ctx = agent.repo.get_research_context(agent.project_id)
    assert ctx["topic"] == "宗族与乡村治理"
    # 广告面锚定实际下发 LLM 的 tools（run_tool_loop 经 kw 透传，seen 已记录）：
    # 状态 schema 在 chat() 内与 registry schema 拼接，_build_tools() 不含它
    tools_names = [t["function"]["name"] for t in agent.llm.seen[0][1]["tools"]]
    assert "update_research_context" in tools_names


def test_cli_state_tool_bad_patch_rejected_not_fatal(fake_config):
    """F02：非法 patch 折 invalid_arguments 包络，回合不炸、后续轮正常。"""
    agent = FieldAgent(fake_config)
    agent.create_project()
    agent.llm = ScriptedLLM([
        {"content": "", "tool_calls": [
            {"id": "c1", "name": "update_research_context",
             "arguments": {"patch": {"unknown_field": "x"}}}]},
        {"content": "字段不合法，我再问。", "tool_calls": None},
    ])
    out = agent.chat("随便写点")
    assert "字段不合法" in out["reply"]
    events = out["tool_events"]
    assert events and events[0]["ok"] is False
