"""build_project_brief：F04 有界摘要（纯 state 推导，无 repo 依赖）。"""
from fieldwork_agent.kernel.guidance.brief import build_project_brief


def _state(**over):
    base = {
        "research_context": {"discipline": "社会学", "topic": "宗族与乡村治理",
                              "research_questions": "RQ1；RQ2",
                              "time_window": "", "extras": {}},
        "field_sites": [{"name": "李村", "confirmed": True}],
        "informants": [{"type": "村干部", "code": "INF-01"}],
        "proposals": [{"id": "prop-1", "version": 2}],
        "cached_searches": [],
    }
    base.update(over)
    return base


def test_brief_covers_sections_and_bounds():
    brief = build_project_brief(
        _state(cached_searches=[{"id": f"s{i}", "tool": "search_pois",
                                 "query": f"q" * 80} for i in range(9)]))
    assert "宗族与乡村治理" in brief and "RQ1；RQ2" in brief
    assert "李村（已确认）" in brief
    assert "访谈对象 1 位" in brief
    assert "方案草稿 1 份" in brief
    assert "尾部 5 条" in brief          # 快照只留尾部 5 条并标注
    assert brief.count("q" * 80) <= 5    # 有界


def test_brief_marks_unconfirmed_and_gaps():
    brief = build_project_brief(
        _state(research_context={"discipline": "", "topic": "", "extras": {}},
               field_sites=[{"name": "王镇", "confirmed": False}]))
    assert "王镇" in brief and "（已确认）" not in brief
    assert "待补" in brief               # 定位缺口提示


def test_brief_candidate_windows_flag():
    brief = build_project_brief(_state(research_context={
        "discipline": "", "topic": "", "extras": {},
        "candidate_windows": [{"start": "2026-07-01", "end": "2026-07-03",
                               "reason": "r"}]}))
    assert "2026-07-01" in brief and "未经用户确认" in brief
