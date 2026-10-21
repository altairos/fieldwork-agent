"""文献检索宿主集成：完整引用摘要、共同提示词、并发预算与回合隔离。"""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from fieldwork_agent.kernel.models import Envelope, LiteratureItem, LiteraturePage, ToolError
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.sources.openalex import OpenAlexClient
from fieldwork_agent.runtime.literature import DuplicateQueryGuard, LiteratureSearchBudget
from fieldwork_agent.runtime.loop import _tool_content, run_tool_loop
from fieldwork_agent.web_host.turn_manager import TurnManager


def test_literature_summary_keeps_citation_and_abstract_in_valid_json():
    page = LiteraturePage(items=[LiteratureItem(
        title="Kinship and change", authors=["Researcher"], year=2024,
        doi="10.1234/kinship", abstract="Study of kinship and rural transformation. " * 100,
        matched_queries=["核心"])], total=10000)
    content = _tool_content("search_literature", Envelope(ok=True, data=page))
    summary = json.loads(content)
    assert summary["returned_count"] == summary["summarized_count"] == 1
    assert "total" not in summary  # 旧缓存里的meta.count不会冒充已返回量
    item = summary["items"][0]
    assert item["doi"] == "10.1234/kinship" and item["year"] == 2024
    assert item["authors"] == ["Researcher"] and len(item["abstract"]) == 500
    assert item["screening_basis"] == "abstract_excerpt"
    assert len(content) <= 12000


def test_no_abstract_is_explicitly_unverified_and_large_summary_stays_valid():
    page = LiteraturePage(items=[LiteratureItem(
        title="K" * 1000, authors=["A" * 1000] * 10, doi="10.1/" + "d" * 1500)
        for _ in range(20)])
    summary = json.loads(_tool_content("search_literature", Envelope(ok=True, data=page)))
    assert summary["returned_count"] == 20
    assert 0 < summary["summarized_count"] < 8
    assert summary["items"][0]["screening_basis"] == "title_only_unverified"


def test_no_result_keeps_feedback_in_llm_message(monkeypatch):
    monkeypatch.setattr(OpenAlexClient, "_get", lambda *a: {"meta": {"count": 0}, "results": []})
    env = OpenAlexClient().search_works("kinship")
    summary = json.loads(_tool_content("search_literature", env))
    assert not summary["ok"] and summary["chinese_sources"]
    assert summary["query_reports"][0]["assessment"] == "empty"


def test_literature_content_limit_caps_at_6000():
    """P2-c：文献单条结果上限 6000 字符，且仍是完整 JSON（不切半截）。"""
    items = [LiteratureItem(
        title="Kinship and change " * 20, authors=["Researcher"], year=2024,
        doi=f"10.1234/kinship{i}", abstract="Study of kinship. " * 300,
        matched_queries=["核心"]) for i in range(10)]
    page = LiteraturePage(items=items, total=10000)
    content = _tool_content("search_literature", Envelope(ok=True, data=page))

    assert len(content) <= 6000
    summary = json.loads(content)  # 完整 JSON
    assert summary["tool"] == "search_literature"
    assert summary["items"]


def test_duplicate_query_guard_blocks_repeat_query():
    """P2-c：同回合完全重复的搜索请求直接拒绝，不消耗工具额度。"""
    calls = []
    guarded = DuplicateQueryGuard().wrap(
        lambda n, a: (calls.append((n, a)), Envelope(ok=True))[1])

    assert guarded("search_keyword", {"query": "大理 民宿"}).ok
    env = guarded("search_keyword", {"query": "  大理   民宿 "})  # 空白归一后同查询
    assert env.error is not None and env.error.code == "duplicate_query"
    assert len(calls) == 1


def test_duplicate_query_guard_retries_after_failure():
    """P2-4：失败不记账——瞬时故障后同一查询必须可重试（旧实现已 add 拿不回来）。"""
    responses = iter([
        Envelope(ok=False, error=ToolError(code="network", message="reset")),
        Envelope(ok=True),
    ])
    calls = []

    def dispatch(name, args):
        calls.append(name)
        return next(responses)

    guarded = DuplicateQueryGuard().wrap(dispatch)
    first = guarded("search_keyword", {"query": "大理 民宿"})
    assert not first.ok
    assert guarded("search_keyword", {"query": "大理 民宿"}).ok
    assert len(calls) == 2
    # 成功之后同查询仍被挡
    assert guarded("search_keyword", {"query": "大理 民宿"}).error.code == "duplicate_query"


def test_duplicate_query_guard_allows_distinct_and_uncovered():
    """不同查询/不同工具/未覆盖工具不受影响。"""
    guarded = DuplicateQueryGuard().wrap(lambda n, a: Envelope(ok=True))

    assert guarded("search_keyword", {"query": "大理 民宿"}).ok
    assert guarded("search_keyword", {"query": "丽江 民宿"}).ok
    assert guarded("search_ai", {"query": "大理 民宿"}).ok       # 工具不同
    assert guarded("amap_text_search", {"keywords": "卫生院", "city": "大理"}).ok
    assert guarded("amap_text_search", {"keywords": "卫生院", "city": "丽江"}).ok
    assert guarded("search_hotels", {"dest_name": "大理"}).ok
    assert guarded("search_hotels", {"dest_name": "大理"}).ok     # 未覆盖，不拦


def test_budget_blocks_repeats_and_reserves_return_slots():
    calls = []
    bounded = LiteratureSearchBudget().wrap(lambda n, a: (calls.append(a), Envelope(ok=True))[1])
    assert bounded("search_literature", {"query": "kinship", "max_results": 20}).ok
    assert bounded("search_literature", {"query": "kinship", "max_results": 5}).error.code == "literature_duplicate_query"
    assert bounded("search_literature", {"query": "lineage", "max_results": 20}).ok
    assert [c["max_results"] for c in calls] == [20, 4]
    assert bounded("search_literature", {"query": "rural"}).error.code == "literature_budget_exceeded"
    assert bounded("other_tool", {}).ok  # 不阻止非文献工具


def test_parallel_budget_limits_matrix_requests():
    calls = []
    bounded = LiteratureSearchBudget().wrap(lambda n, a: (calls.append(a), Envelope(ok=True))[1])

    def invoke(i):
        return bounded("search_literature", {"query": "kinship", "search_matrix": [
            {"label": f"{i}-{j}", "groups": [[f"term {i} {j}"]]} for j in range(4)]})
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(invoke, range(4)))
    assert sum(r.ok for r in results) == 2 and len(calls) == 2
    assert all(r.ok or r.error.code == "literature_budget_exceeded" for r in results)


def test_matrix_relabel_does_not_bypass_duplicate_guard():
    bounded = LiteratureSearchBudget().wrap(lambda *a: Envelope(ok=True))
    assert bounded("search_literature", {"query": "主题甲", "search_matrix": [
        {"label": "核心", "groups": [["kinship"]]}]}).ok
    env = bounded("search_literature", {"query": "主题乙", "search_matrix": [
        {"label": "改个名字", "groups": [["kinship"]]}]})
    assert env.error.code == "literature_duplicate_query"


class ScriptedLLM:
    def __init__(self):
        self.step = 0
        self.messages = []

    def chat(self, messages, **kwargs):
        self.messages = list(messages)
        self.step += 1
        if self.step == 1:
            return {"content": "", "tool_calls": [
                {"id": str(i), "name": "search_literature", "arguments": {"query": f"kinship {i}"}}
                for i in range(4)]}
        return {"content": "已完成初筛", "tool_calls": None, "finish_reason": "stop"}

    def chat_stream(self, messages, **kwargs):
        yield self.chat(messages, **kwargs)


def test_cli_loop_enforces_literature_budget_and_resets_each_turn():
    calls = []
    def dispatch(name, args):
        calls.append(args)
        return Envelope(ok=True, data=LiteraturePage())
    for _ in range(2):
        llm = ScriptedLLM()
        result = run_tool_loop(llm, [], dispatch, [])
        assert result.reply == "已完成初筛"
        feedback = [json.loads(m["content"]) for m in llm.messages if m["role"] == "tool"]
        assert len(feedback) == 4
        assert feedback[-1]["error"]["code"] == "literature_budget_exceeded"
    assert len(calls) == 6


def test_web_parallel_loop_enforces_same_budget(monkeypatch, tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    tm = TurnManager(root=tmp_path / "projects")
    turn = tm.create_turn("p1")
    requests = []

    def fake_get(self, path, params):
        requests.append(params)
        return {"meta": {"count": 15000}, "results": [{
            "id": params["search.title_and_abstract"], "title": "Kinship and change"}]}
    monkeypatch.setattr(OpenAlexClient, "_get", fake_get)
    from fieldwork_agent.web_host.agent.loop import run_turn
    llm = ScriptedLLM()
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "找宗族文献", tm, llm, None))
    assert len(requests) == 3
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    done = [e for e in events if e.event == "tool_completed"]
    assert len(done) == 4 and sum(e.data["ok"] for e in done) == 3
    assert any(e.data.get("error", {}).get("code") == "literature_budget_exceeded"
               for e in done if not e.data["ok"])
    assert all(e.data["data"]["total"] == 1 for e in done if e.data["ok"])


def test_all_hosts_receive_shared_literature_guidance():
    from fieldwork_agent.core.orchestrator import _system_prompt
    from fieldwork_agent.kernel.guidance.literature import LITERATURE_GUIDANCE
    from fieldwork_agent.mcp_server.prompts import get_prompt
    from fieldwork_agent.web_host.agent.context import build_system_prompt
    assert LITERATURE_GUIDANCE in _system_prompt("research_positioning", "")
    assert LITERATURE_GUIDANCE in build_system_prompt("research_positioning", "")
    assert LITERATURE_GUIDANCE in get_prompt("fieldwork_interview")["messages"][0]["content"]
