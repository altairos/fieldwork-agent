"""引导访谈策略接线与运行时回归；脚本模型不用于证明真实模型的语义质量。"""
import asyncio
import json

import pytest
from fieldwork_agent.core.orchestrator import FieldAgent, _system_prompt
from fieldwork_agent.kernel import assets
from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.kernel.guidance.interview import INTERVIEW_GUIDANCE, INTERVIEW_TURN_REMINDER
from fieldwork_agent.kernel.guidance.rule_replies import rule_based_reply
from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server import prompts, resources
from fieldwork_agent.runtime.loop import run_tool_loop
from fieldwork_agent.web_host.agent.context import build_messages, build_system_prompt
from fieldwork_agent.web_host.agent.loop import _looks_like_preamble, run_turn
from fieldwork_agent.web_host.turn_manager import TurnManager

_KNOWN = {
    "title": "访谈测试", "topic": "婚丧事务协商", "purpose": "course", "discipline": "人类学",
    "research_questions": "微信群中的协商如何进入婚丧事务的决策过程？",
    "scale": "单人调研", "budget": "2万元", "time_window": "2027年7月",
}


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((list(messages), kwargs))
        if not self.replies:
            pytest.fail("完整回复后不应自动追问或再次调用模型")
        return self.replies.pop(0)

    def chat_stream(self, messages, **kwargs):
        reply = self.chat(messages, **kwargs)
        if reply.get("content"):
            yield {"delta": reply["content"]}
        yield reply


def _reply(content):
    return {"content": content, "tool_calls": None, "finish_reason": "stop", "usage": {}}


def test_all_hosts_load_same_interview_policy_after_phase_guidance():
    for phase in phases.PHASES:
        for rendered in (
            _system_prompt(phase, "快照"), build_system_prompt(phase, "快照"),
            prompts.get_prompt("fieldwork_interview", {"phase": phase})["messages"][0]["content"],
        ):
            assert rendered.count(INTERVIEW_GUIDANCE) == 1
            assert rendered.index(INTERVIEW_GUIDANCE) > rendered.index(phases.phase_prompt(phase))


def test_private_phase_override_keeps_shared_policy(tmp_path):
    legacy = "私有阶段：一次只问2-3个问题，必须先追问一轮。"
    (tmp_path / "system_prompts.json").write_text(
        json.dumps({phase: legacy for phase in phases.PHASES}), encoding="utf-8")
    assets.reload(tmp_path)
    for rendered in (
        _system_prompt("research_positioning", ""),
        build_system_prompt("research_positioning", ""),
        prompts.get_prompt("fieldwork_interview")["messages"][0]["content"],
    ):
        assert legacy in rendered and INTERVIEW_GUIDANCE in rendered
        assert rendered.index(INTERVIEW_GUIDANCE) > rendered.index(legacy)
        assert "以本共享策略为准" in rendered


def test_mcp_question_library_is_optional_material(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path)
    rendered = resources.read_resource(repo, "fieldwork://guidance/questions/research_positioning")
    assert rendered["ok"]
    assert "不按序逐项询问" in rendered["text"]
    assert "具体经历" in rendered["text"]


@pytest.mark.parametrize("text", [
    "我先了解一个线索：有没有一件具体经历让你关注宗族组织？",
    "我先了解一个线索：你更希望整理哪类材料？\n[[options:材料]]观察记录|文献笔记[[/options]]",
    "我先按现有信息整理了研究问题。",
    "我先确认一个线索: What would you like to understand?",
    "先定两项：\n[[options:用途]]开题|申报[[/options]]\n[[options:学科]]人类学|社会学[[/options]]",
])
def test_completed_interview_response_is_not_a_preamble(text):
    assert not _looks_like_preamble(text)


@pytest.mark.parametrize("text", ["我这就去查：", "先问最实际的：", "我先把信息写入项目状态"])
def test_unfinished_promises_still_need_repair(text):
    assert _looks_like_preamble(text)


def test_cli_accepts_direct_draft_and_keeps_turn_reminder(fake_config):
    agent = FieldAgent(fake_config)
    pid = agent.create_project("interview")
    agent.repo.update_research_context(pid, _KNOWN)
    paragraph = "本研究关注微信群中的协商如何进入婚丧事务的决策过程，将结合讨论记录与实际安排核验。"
    llm = ScriptedLLM([_reply(paragraph)])
    agent.llm = llm
    result = agent.chat("请据此写一段研究问题说明")
    assert result["reply"] == paragraph and not result["tool_events"]
    assert len(llm.calls) == 1
    messages = llm.calls[0][0]
    assert messages[-1] == {"role": "system", "content": INTERVIEW_TURN_REMINDER}
    assert _KNOWN["research_questions"] in messages[0]["content"]
    assert agent.repo.get_research_context(pid)["research_questions"] == _KNOWN["research_questions"]


@pytest.mark.parametrize("content", [
    "我先了解一个线索：有没有一件经历或一份材料，让你关注这个主题？",
    "我先了解一个线索：你更希望整理哪类材料？\n[[options:材料]]观察记录|文献笔记[[/options]]",
    "本研究关注微信群协商与实际婚丧事务安排之间的联系；具体机制仍待田野材料核验。",
])
def test_web_interview_can_end_without_forced_tools_or_questions(tmp_path, content):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    repo.update_research_context("p1", _KNOWN)
    tm = TurnManager(root=tmp_path / "projects")
    turn = tm.create_turn("p1")
    llm = ScriptedLLM([_reply(content)])
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "帮我继续整理这个研究", tm, llm, None))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    assert len(llm.calls) == 1
    assert not any(e.event == "tool_started" for e in events)
    done = next(e for e in events if e.event == "turn_done")
    assert not done.data["incomplete"]
    if "[[options:" in content:
        assert done.data["quick_reply_groups"] == [{"label": "材料", "options": ["观察记录", "文献笔记"]}]
    else:
        assert not done.data["quick_reply_groups"]


@pytest.mark.parametrize("host", ["cli", "web"])
def test_exploratory_rounds_do_not_force_closure_or_adopt_candidates(tmp_path, fake_config, host):
    """探索可跨轮继续；宿主不补问、不推进阶段，也不把助手角度记成已采纳问题。"""
    replies = [
        "可以比较咖啡馆作为独处空间与熟人交往场所的差别。前者关注人如何暂时退出熟人关系，"
        "后者关注关系如何在店内延续；观察座位、停留与交谈方式，能为两种解释提供不同线索。",
        "还可以沿经营劳动展开：店主希望营造的氛围，与维持客流所需的日常工作未必一致。"
        "跟随一次开店到打烊的过程，可以把空间形象和具体劳动联系起来，暂不必把它定成选题。",
    ]
    llm = ScriptedLLM([_reply(reply) for reply in replies])
    if host == "cli":
        agent = FieldAgent(fake_config)
        pid = agent.create_project("exploration")
        agent.llm = llm
        repo = agent.repo
    else:
        repo = JsonRepository(tmp_path / "projects")
        pid = "exploration"
        repo.create(pid)
        tm = TurnManager(tmp_path / "projects")
    repo.update_research_context(pid, {"title": "探索测试", "topic": "县城咖啡馆"})
    for message, expected in zip(("先聊聊这个题目有什么值得琢磨", "先不定问题，再展开另一个角度"), replies, strict=True):
        if host == "cli":
            result = agent.chat(message)
            assert result["reply"] == expected and not result["tool_events"]
        else:
            turn = tm.create_turn(pid)
            asyncio.run(run_turn(repo, pid, turn.turn_id, message, tm, llm, None))
            assert not any(e.event == "tool_started" for e in tm.replay(pid, turn.turn_id, 0))
        assert repo.get_messages(pid)[-1]["content"] == expected
        assert repo.get_phase(pid) == "research_positioning"
        assert not repo.get_research_context(pid).get("research_questions")
    assert len(llm.calls) == 2


def test_preamble_repair_can_finish_with_one_question(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    repo.update_research_context("p1", {"title": "访谈测试"})
    tm = TurnManager(root=tmp_path / "projects")
    turn = tm.create_turn("p1")
    question = "有没有一件具体经历，让你开始关注这个主题？"
    llm = ScriptedLLM([_reply("先问最实际的："), _reply(question)])
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "我想研究宗族变迁", tm, llm, None))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    assert len(llm.calls) == 2
    assert not any(e.event == "tool_started" for e in events)
    nudge = llm.calls[1][0][-1]["content"]
    assert "只有需要外部真实数据时才调用工具" in nudge
    assert repo.get_messages("p1")[-1]["content"] == "先问最实际的：" + question


def test_tool_failure_can_finish_with_available_result():
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [{"id": "t1", "name": "search_hotels", "arguments": {}}]},
        _reply("住宿报价尚未取得；现有研究问题说明已整理，住宿费用保留待核验。"),
    ])
    result = run_tool_loop(llm, [], lambda *args: Envelope(
        ok=False, error=ToolError(code="flyai_unavailable", message="no cli")), [])
    assert len(llm.calls) == 2 and result.reply.endswith("待核验。")
    tool = next(m["content"] for m in llm.calls[1][0] if m["role"] == "tool")
    assert "已有信息足够回应时" in tool and "不强制追问研究信息" in tool


@pytest.mark.parametrize("phase,context,expected", [
    ("research_positioning", {}, "具体线索"),
    ("research_positioning", {"topic": "宗族变迁"}, "具体困惑"),
    ("field_site_profiles", {}, "目标区域"),
    ("itinerary_planning", {"departure_city": "上海"}, "日期出发"),
])
def test_rule_mode_selects_one_missing_question(phase, context, expected):
    reply = rule_based_reply(phase, context)
    assert expected in reply and reply.count("？") == 1


def test_rule_mode_can_finish_and_reuse_legacy_extras():
    assert "？" not in rule_based_reply("research_positioning", _KNOWN)
    assert "？" not in rule_based_reply("itinerary_planning", {
        "departure_city": "上海", "time_window": "2027年7月"})
    assert "基础信息已具备" in rule_based_reply("research_positioning", {
        "topic": "宗族", "purpose": "course", "extras": {"research_questions": "RQ"}})


def test_cli_rule_mode_receives_current_context(fake_config):
    agent = FieldAgent(fake_config)
    pid = agent.create_project("interview")
    agent.repo.update_research_context(pid, {**_KNOWN, "budget": ""})
    result = agent.chat("继续")
    assert result["reply"] == rule_based_reply("research_positioning", {**_KNOWN, "budget": ""})
    assert result["reply"].count("？") == 0 and "预算范围" not in result["reply"]


def test_web_rule_mode_receives_current_context(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    repo.update_research_context("p1", {**_KNOWN, "budget": ""})
    tm = TurnManager(root=tmp_path / "projects")
    turn = tm.create_turn("p1")
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "继续", tm, None, None))
    reply = repo.get_messages("p1")[-1]["content"]
    assert "基础信息已具备" in reply and reply.count("？") == 0
    assert "规则引导文案" in reply


def test_web_turn_context_and_policy_do_not_pollute_saved_history(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path)
    repo.create("p1")
    repo.update_research_context("p1", _KNOWN)
    repo.append_message("p1", "assistant", "已有说明已整理。")
    messages = build_messages(repo, "p1", "请直接起草", "research_positioning")
    assert INTERVIEW_TURN_REMINDER in messages[-1]["content"]
    assert "没有向用户提问就不生成选项块" in messages[-1]["content"]
    assert [m["role"] for m in repo.get_messages("p1")] == ["assistant"]
