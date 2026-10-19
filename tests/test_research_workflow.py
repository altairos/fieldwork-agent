"""研究迭代、任务准备条件、工具分工及双存储端的实际接线。"""
import asyncio

import pytest
from fieldwork_agent.core.orchestrator import FieldAgent, _system_prompt
from fieldwork_agent.kernel.context_patch import context_patch_schema, sanitize_context_patch
from fieldwork_agent.kernel.guidance.brief import build_project_brief
from fieldwork_agent.kernel.guidance.phases import check_advance
from fieldwork_agent.kernel.guidance.research import task_readiness
from fieldwork_agent.kernel.models import Envelope, ResearchContext
from fieldwork_agent.kernel.registry import build_tool_schemas, capabilities
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository
from fieldwork_agent.kernel.research_state import scope_fingerprint
from fieldwork_agent.kernel.sources.flyai import FlyAIClient, search_ai, search_keyword
from fieldwork_agent.kernel.sources.openalex import OpenAlexClient
from fieldwork_agent.kernel.tool_usage import TOOL_USAGE_GUIDANCE, check_tool_domain
from fieldwork_agent.mcp_server import dispatch as mcp_dispatch
from fieldwork_agent.mcp_server.prompts import get_prompt
from fieldwork_agent.mcp_server.state_tools import STATE_TOOLS, build_handlers
from fieldwork_agent.runtime.state_tools import _UPDATE_SCHEMA, build_project_state_tools
from fieldwork_agent.web_host.agent.context import build_messages, build_system_prompt
from fieldwork_agent.web_host.agent.dispatch import build_dispatch_fn
from fieldwork_agent.web_host.agent.loop import run_turn
from fieldwork_agent.web_host.turn_manager import TurnManager


class TravelSpy:
    def __init__(self):
        self.calls = []

    def search_keyword(self, query):
        self.calls.append(query)
        return Envelope(ok=True)

    search_ai = search_keyword


@pytest.fixture(params=["json", "sqlite"])
def repo(request, tmp_path):
    result = (JsonRepository(tmp_path / "projects") if request.param == "json"
              else SqliteRepository(tmp_path / "db.sqlite"))
    result.create("p1")
    return result


@pytest.mark.parametrize("name", ["search_keyword", "search_ai"])
@pytest.mark.parametrize("query", [
    "白族银器传承相关学术论文", "检索宗族变迁文献", "酒店服务质量文献综述",
    "Bai silver craft ethnography", "scholarly articles about kinship", "research papers on hotel booking",
])
def test_explicit_scholarly_misuse_blocked_without_travel_call(name, query):
    spy = TravelSpy()
    fn = search_keyword if name == "search_keyword" else search_ai
    env = fn(query=query, client=spy)
    assert not env.ok and env.error.code == "tool_domain_mismatch"
    assert "search_literature" in env.error.hint and not spy.calls


@pytest.mark.parametrize("query", [
    "适合长期驻村的便宜住宿", "云南包车租车", "民族志博物馆门票", "ethnography museum tickets",
    "学术会议酒店预订", "银器传承",  # 歧义主题只靠用途说明，不宣称能确定语义
])
def test_legitimate_travel_and_ambiguous_queries_are_not_overblocked(query):
    assert check_tool_domain("search_keyword", query) is None


def test_direct_flyai_client_also_blocks_before_execute(monkeypatch):
    client = FlyAIClient()
    monkeypatch.setattr(client, "_execute", lambda *a: pytest.fail("误调用不得出网"))
    assert client.search_keyword("宗族变迁文献").error.code == "tool_domain_mismatch"
    assert client.search_ai("学术材料").error.code == "tool_domain_mismatch"


def test_three_hosts_dispatch_same_domain_error(fake_config, monkeypatch):
    spy = TravelSpy()
    agent = FieldAgent(fake_config)
    agent.flyai = spy
    args = {"query": "收集白族民族志文献"}
    assert agent.client_kernel_dispatch("search_keyword", args).error.code == "tool_domain_mismatch"
    assert build_dispatch_fn(spy, None)("search_keyword", args).error.code == "tool_domain_mismatch"
    real = capabilities()["search_keyword"].fn
    monkeypatch.setitem(mcp_dispatch._CAPABILITY_FNS, "search_keyword", real)
    monkeypatch.setitem(mcp_dispatch._CLIENTLESS_SINGLETONS, "search_keyword", real)
    result = mcp_dispatch.call_capability("search_keyword", args, {"client": spy})
    assert result["error"]["code"] == "tool_domain_mismatch" and not spy.calls


def test_tool_roles_are_in_hosts_and_discovery_schema():
    assert TOOL_USAGE_GUIDANCE in _system_prompt("field_site_profiles", "")
    assert TOOL_USAGE_GUIDANCE in build_system_prompt("field_site_profiles", "")
    assert TOOL_USAGE_GUIDANCE in get_prompt("fieldwork_interview")["messages"][0]["content"]
    for tool in build_tool_schemas():
        if tool["function"]["name"] in {"search_keyword", "search_ai"}:
            assert "旅行商品" in tool["function"]["description"]
            assert "search_literature" in tool["function"]["description"]


def test_working_state_tool_schemas_expose_real_enums():
    expected = context_patch_schema()["properties"]["research_notes"]
    runtime = _UPDATE_SCHEMA["function"]["parameters"]["properties"]["patch"]
    mcp = next(t for t in STATE_TOOLS if t.name == "update_research_context").model_dump(
        by_alias=True)["inputSchema"]["properties"]["patch"]
    assert runtime["properties"]["research_notes"] == mcp["properties"]["research_notes"] == expected
    assert "literature_gap" in expected["items"]["properties"]["kind"]["enum"]
    assert "resource_path" not in expected["items"]["properties"]["kind"]["enum"]


def test_task_readiness_uses_task_specific_information():
    topic = {"topic": "宗族组织"}
    assert task_readiness(topic, "literature")["ready"]
    assert not task_readiness(topic, "field_site_comparison")["ready"]
    ctx = {**topic, "research_questions": "宗族成员如何协商公共事务？"}
    assert task_readiness(ctx, "field_site_comparison")["ready"]
    assert task_readiness(ctx, "method_design")["ready"]
    assert not task_readiness(ctx, "itinerary")["ready"]
    assert "budget" not in task_readiness(ctx, "field_site_comparison")["missing"]
    assert not task_readiness({"topic": "宗族", "research_questions": "宗族"}, "method_design")["ready"]


def test_topic_change_requires_question_reconfirmation_and_can_revisit_positioning():
    original = {"topic": "宗族", "research_questions": "成员如何协商公共事务？"}
    changed = {**original, **sanitize_context_patch({"topic": "学校组织"}, original)}
    assert "research_questions_reconfirmation" in task_readiness(changed, "method_design")["missing"]
    transition = check_advance("finished", ResearchContext.model_validate(changed), [], [], True, True, True)
    assert transition.can_advance and transition.to_phase == "research_positioning"
    reaffirmed = {**changed, **sanitize_context_patch({"research_questions": original["research_questions"]}, changed)}
    assert task_readiness(reaffirmed, "method_design")["ready"]
    assert task_readiness(original, "field_site_comparison")["ready"]  # 旧项目读取兼容


@pytest.mark.parametrize("legacy", [False, True])
def test_withdrawing_a_confirmed_question_keeps_rejection_and_reopens_positioning(legacy):
    question = "成员如何协商公共事务？"
    original = ({"topic": "宗族", "extras": {"research_questions": question, "region": "云南"}}
                if legacy else {"topic": "宗族", "research_questions": question})
    with pytest.raises(ValueError, match="撤回"):
        sanitize_context_patch({"research_questions": ""}, original)
    clean = sanitize_context_patch({"research_questions": "", "research_notes": [
        _note(question, origin="user", status="rejected", basis="用户明确说不再研究此问题")]}, original)
    ctx = {**original, **clean}
    assert not task_readiness(ctx, "field_site_comparison")["ready"]
    assert ctx["research_notes"][0]["content"] == question
    if legacy:
        assert ctx["extras"] == {"region": "云南"}
    result = check_advance("finished", ResearchContext.model_validate(ctx), [], [], True, True, True)
    assert result.can_advance and result.to_phase == "research_positioning"


def _note(content="是否需要比较线下协调", **extra):
    return {"content": content, "origin": "agent", "kind": "question", "status": "proposed", **extra}


def test_notes_are_incremental_auditable_and_separate_from_confirmed_questions(repo):
    repo.update_research_context("p1", {"topic": "宗族", "research_questions": "RQ"})
    _, handlers = build_project_state_tools(repo, "p1")
    assert handlers["update_research_context"]({"research_notes": [_note()]}).ok
    first = repo.get_research_context("p1")["research_notes"][0]
    assert first["scope_fingerprint"] == scope_fingerprint(repo.get_research_context("p1"))
    assert handlers["update_research_context"]({"research_notes": [
        {"id": first["id"], "status": "rejected", "basis": "用户明确说不做线上线下比较"},
        _note("本次摘要不足以证明研究空白", origin="literature", kind="literature_gap",
              references=["https://openalex.org/W123"]),
    ]}).ok
    state = repo.get_research_context("p1")
    assert len(state["research_notes"]) == 2
    assert state["research_notes"][0]["status"] == "rejected"
    assert state["research_questions"] == "RQ"
    brief = build_project_brief(repo.get_state("p1"))
    assert "rejected" in brief and "不做线上线下比较" in brief and "W123" in brief
    repo.update_research_context("p1", {"topic": "学校"})
    assert "旧范围/未绑定" in build_project_brief(repo.get_state("p1"))


@pytest.mark.parametrize("notes", [
    [], [_note(origin="unknown")], [_note(content=" ")], [_note(status="adopted")],
    [_note(origin="literature")], [_note(unexpected="x")], [_note()] * 9,
])
def test_invalid_notes_reject_entire_patch(repo, notes):
    _, handlers = build_project_state_tools(repo, "p1")
    before = repo.get_research_context("p1")
    env = handlers["update_research_context"]({"budget": "5万", "research_notes": notes})
    assert not env.ok and env.error.code == "invalid_arguments"
    assert repo.get_research_context("p1") == before


def test_note_rewrite_requires_new_record_and_old_reference_stays(repo):
    repo.update_research_context("p1", sanitize_context_patch({"research_notes": [_note()]}))
    current = repo.get_research_context("p1")
    with pytest.raises(ValueError, match="superseded"):
        sanitize_context_patch({"research_notes": [{"id": current["research_notes"][0]["id"], "content": "新问题"}]}, current)


def test_rejected_direction_cannot_silently_become_a_candidate_again(repo):
    repo.update_research_context("p1", sanitize_context_patch({"research_notes": [
        _note(status="rejected", basis="用户说不做这个方向")]}))
    current = repo.get_research_context("p1")
    with pytest.raises(ValueError, match="新的basis"):
        sanitize_context_patch({"research_notes": [
            {"id": current["research_notes"][0]["id"], "status": "proposed"}]}, current)


def test_note_origin_cannot_be_relabelled_as_user_fact(repo):
    repo.update_research_context("p1", sanitize_context_patch({"research_notes": [_note()]}))
    current = repo.get_research_context("p1")
    with pytest.raises(ValueError, match="来源不可改写"):
        sanitize_context_patch({"research_notes": [
            {"id": current["research_notes"][0]["id"], "origin": "user"}]}, current)


def test_supplied_information_is_no_longer_shown_as_deferred():
    brief = build_project_brief({"research_context": {
        "budget": "5万元", "interview_state": {"deferred_fields": ["budget"]}}})
    assert "用户暂缓提供" not in brief


def test_memory_and_notes_do_not_stale_proposal_but_research_change_does(repo):
    repo.update_research_context("p1", {"topic": "宗族", "research_questions": "RQ"})
    repo.save_proposal("p1", "# 方案", kind="proposal", version="draft")
    repo.confirm_proposal("p1")
    patch = sanitize_context_patch({"research_notes": [_note()], "interview_state": {
        "current_focus": "先梳理材料", "open_questions": ["如何获得观察材料"], "deferred_fields": ["budget"]}},
        repo.get_research_context("p1"))
    repo.update_research_context("p1", patch)
    assert not repo.is_proposal_confirmation_stale("p1")
    repo.update_research_context("p1", sanitize_context_patch({"topic": "学校"}, repo.get_research_context("p1")))
    assert repo.is_proposal_confirmation_stale("p1")


def test_mcp_can_record_structured_working_state(repo):
    handlers = build_handlers(repo)
    result = handlers["update_research_context"]("p1", {"research_notes": [_note()],
        "interview_state": {"current_focus": "待观察的现象", "deferred_fields": ["budget"]}})
    assert result["ok"]
    assert result["data"]["research_notes"][0]["status"] == "proposed"


class ScriptedLLM:
    def __init__(self, steps):
        self.steps = iter(steps)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(list(messages))
        return next(self.steps)

    def chat_stream(self, messages, **kwargs):
        yield self.chat(messages, **kwargs)


def _tool(name, args, id="1"):
    return {"content": "", "tool_calls": [{"id": id, "name": name, "arguments": args}]}


def _answer(text="已整理可研究的问题。"):
    return {"content": text, "tool_calls": None, "finish_reason": "stop", "usage": {}}


def test_cli_recovers_wrong_tool_and_caches_literature_with_scope(fake_config, monkeypatch):
    agent = FieldAgent(fake_config)
    agent.create_project("p1")
    agent.repo.update_research_context("p1", {"topic": "宗族", "title": "范围测试"})
    spy = TravelSpy()
    agent.flyai = spy
    monkeypatch.setattr(OpenAlexClient, "_get", lambda *a: {"meta": {"count": 100}, "results": [
        {"id": "https://openalex.org/W123", "title": "Kinship"}]})
    llm = ScriptedLLM([_tool("search_keyword", {"query": "宗族变迁学术论文"}),
                       _tool("search_literature", {"query": "kinship"}, "2"), _answer()])
    agent.llm = llm
    result = agent.chat("帮我找既有研究")
    assert not spy.calls and len(result["tool_events"]) == 2
    assert "tool_domain_mismatch" in llm.calls[1][-1]["content"]
    cached = agent.repo.list_cached_searches("p1")
    assert len(cached) == 1 and cached[0]["tool"] == "search_literature"
    assert "当前范围" in build_project_brief(agent.repo.get_state("p1"), cached_searches=cached)
    ctx = agent.repo.get_research_context("p1")
    assert ctx["interview_state"]["last_user_message"] == "帮我找既有研究"
    agent.repo.update_research_context("p1", {"topic": "学校"})
    assert "旧范围或未绑定" in agent._snapshot("p1")


def test_web_writes_memory_and_scope_then_reuses_beyond_short_history(tmp_path, monkeypatch):
    repo = JsonRepository(tmp_path / "projects")
    repo.create("p1")
    repo.update_research_context("p1", {"title": "范围测试", "topic": "宗族"})
    tm = TurnManager(root=tmp_path / "projects")
    turn = tm.create_turn("p1")
    monkeypatch.setattr(OpenAlexClient, "_get", lambda *a: {"meta": {"count": 1}, "results": [
        {"id": "https://openalex.org/W123", "title": "Kinship"}]})
    llm = ScriptedLLM([
        _tool("update_research_context", {"patch": {"interview_state": {
            "deferred_fields": ["budget"], "open_questions": ["如何进入观察场景"]},
            "research_notes": [_note("不研究线上影响", status="rejected", basis="用户明确否定") ]}}),
        _tool("search_literature", {"query": "kinship"}, "2"), _answer(),
    ])
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "预算暂不确定，先看文献", tm, llm, None))
    ctx = repo.get_research_context("p1")
    assert ctx["interview_state"]["last_assistant_message"] == "已整理可研究的问题。"
    assert ctx["interview_state"]["deferred_fields"] == ["budget"]
    cached = repo.list_cached_searches("p1")
    assert cached[0]["tool"] == "search_literature"
    done = next(e for e in tm.replay("p1", turn.turn_id, 0) if e.event == "turn_done")
    assert cached[0]["id"] in done.data["snapshot_ids"]
    messages = build_messages(repo, "p1", "继续", "research_positioning", history_limit=0)
    assert "不研究线上影响" in messages[0]["content"]
    assert "用户暂缓提供" in messages[0]["content"]


def test_cancelled_web_turn_does_not_commit_working_memory(tmp_path):
    repo = JsonRepository(tmp_path / "projects")
    repo.create("p1")
    repo.update_research_context("p1", {"title": "取消测试"})
    before = repo.get_research_context("p1")
    tm = TurnManager(root=tmp_path / "projects")
    turn = tm.create_turn("p1")

    class Cancelled:
        def chat_stream(self, messages, **kwargs):
            tm.get_turn("p1", turn.turn_id).cancel_event.set()
            yield _answer()
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "继续", tm, Cancelled(), None))
    assert repo.get_research_context("p1") == before
