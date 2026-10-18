"""merge_doc_args：仓储研究上下文 → 文档工具入参合并语义（F01）。"""
import pytest
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.runtime.doc_args import merge_doc_args


@pytest.fixture()
def repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


def test_non_doc_tool_passthrough(repo):
    """非文档工具：原样返回，不读仓储。"""
    repo.create("p")
    pid = "p"
    args = {"query": "田野点", "limit": 3}
    assert merge_doc_args(repo, pid, "search_pois", dict(args)) == args


def test_proposal_ctx_repo_fills_empty_slots(repo):
    """proposal：模型给空/缺的槽位由仓储补齐（含 research_questions/time_window）。"""
    repo.create("p")
    pid = "p"
    ctx = repo.get_research_context(pid)
    ctx.update({"discipline": "社会学", "topic": "宗族与乡村治理",
                "research_questions": "RQ1 宗族结构", "purpose": "硕士论文",
                "time_window": "7 月上旬"})
    repo.update_research_context(pid, ctx)
    merged = merge_doc_args(repo, pid, "compose_proposal",
                            {"ctx": {"discipline": "", "topic": ""},
                             "section": "methodology"})
    assert merged["ctx"]["discipline"] == "社会学"
    assert merged["ctx"]["topic"] == "宗族与乡村治理"
    assert merged["ctx"]["research_questions"] == "RQ1 宗族结构"
    assert merged["ctx"]["time_window"] == "7 月上旬"
    assert merged["section"] == "methodology"  # 非合并键原样保留


def test_proposal_extras_key_level_merge(repo):
    """proposal extras：仓储与模型 extras 键级合并（模型键覆盖同名）。"""
    repo.create("p")
    pid = "p"
    ctx = repo.get_research_context(pid)
    ctx["extras"] = {"理论视角": "仓储值"}
    repo.update_research_context(pid, ctx)
    merged = merge_doc_args(repo, pid, "compose_proposal",
                            {"ctx": {"extras": {"方法": "模型值", "理论视角": "模型覆盖"}}})
    assert merged["ctx"]["extras"] == {"理论视角": "模型覆盖", "方法": "模型值"}


def test_guide_repo_slots_fill_empty_model_values(repo):
    """guide：模型 topic 为空补仓储；research_questions/discipline 空则补。"""
    repo.create("p")
    pid = "p"
    ctx = repo.get_research_context(pid)
    ctx.update({"topic": "宗族与乡村治理", "research_questions": "RQ1 宗族结构",
                "discipline": "社会学"})
    repo.update_research_context(pid, ctx)
    merged = merge_doc_args(repo, pid, "compose_interview_guide",
                            {"topic": "", "informant_types": ["村干部"]})
    assert merged["topic"] == "宗族与乡村治理"
    assert merged["research_questions"] == "RQ1 宗族结构"
    assert merged["discipline"] == "社会学"


def test_guide_model_focus_kept_when_nonempty(repo):
    """guide：模型本轮聚焦的 topic 非空时保留（仓储只补空）。"""
    repo.create("p")
    pid = "p"
    ctx = repo.get_research_context(pid)
    ctx.update({"topic": "仓储主题", "research_questions": "仓储RQ"})
    repo.update_research_context(pid, ctx)
    merged = merge_doc_args(repo, pid, "compose_interview_guide",
                            {"topic": "本轮聚焦主题", "informant_types": ["老人"]})
    assert merged["topic"] == "本轮聚焦主题"
    assert merged["research_questions"] == "仓储RQ"


def test_proposal_ctx_view_overrides_repo(repo):
    """M1：ctx_view（仓储+回合暂存合并视图）优先于直读仓储——web 同轮
    update_research_context 后文档工具立即看到新值，不等轮末 commit。"""
    repo.create("p")
    pid = "p"
    view = {"topic": "本轮暂存主题", "discipline": "社会学"}
    merged = merge_doc_args(repo, pid, "compose_proposal",
                            {"ctx": {"topic": ""}, "section": "methodology"},
                            ctx_view=view)
    assert merged["ctx"]["topic"] == "本轮暂存主题"
    assert merged["ctx"]["discipline"] == "社会学"


def test_guide_ctx_view_fills_empty_slots(repo):
    """M1：guide 槽位补齐同样以 ctx_view 为底（暂存值覆盖仓储旧值）。"""
    repo.create("p")
    pid = "p"
    ctx = repo.get_research_context(pid)
    ctx.update({"topic": "仓储旧主题", "research_questions": "RQ 仓储",
                "discipline": "社会学"})
    repo.update_research_context(pid, ctx)
    view = {**repo.get_research_context(pid), "topic": "本轮暂存主题"}
    merged = merge_doc_args(repo, pid, "compose_interview_guide",
                            {"topic": "", "informant_types": ["村干部"]},
                            ctx_view=view)
    assert merged["topic"] == "本轮暂存主题"
    assert merged["research_questions"] == "RQ 仓储"
