"""state — 阶段流转、持久化、知识库、搜索缓存、提案文档、对话溢出。"""
import pytest
from fieldwork_agent.core.state import ProjectState


def test_default_phase_and_order(project):
    assert project.phase == ProjectState.PHASE_RESEARCH_POSITIONING
    assert len(ProjectState.PHASES_ORDER) == 5


def test_invalid_phase_raises(project):
    with pytest.raises(ValueError):
        project.phase = "not_a_real_phase"


def test_advance_phase_walks_order(project):
    seen = [project.phase]
    for _ in range(5):
        project.advance_phase()
        seen.append(project.phase)
    # 到 finished 后不再前进
    assert seen[:5] == ProjectState.PHASES_ORDER
    assert seen[-1] == ProjectState.PHASE_FINISHED


def test_phase_change_records_history_and_persists(make_project, project):
    project.advance_phase()  # → field_site_profiles
    assert project.state["phase_history"][-1]["to"] == ProjectState.PHASE_FIELD_SITE_PROFILES

    # 重新加载同一项目，阶段应被持久化
    reloaded = make_project(project.project_id)
    assert reloaded.phase == ProjectState.PHASE_FIELD_SITE_PROFILES


def test_researcher_info_roundtrip(make_project, project):
    project.set_researcher_info("research_topic", "宗族组织变迁")
    reloaded = make_project(project.project_id)
    assert reloaded.get_researcher_info("research_topic") == "宗族组织变迁"
    assert reloaded.get_all_researcher_info()["research_topic"] == "宗族组织变迁"


def test_knowledge_crud(project):
    kid = project.add_knowledge_entry("访谈提纲", "问题1", category="methods",
                                      tags=["田野"])
    entries = project.get_knowledge_entries()
    assert len(entries) == 1
    assert entries[0]["id"] == kid

    # 按分类过滤
    assert project.get_knowledge_entries(category="methods")
    assert project.get_knowledge_entries(category="misc") == []

    # 删除
    project.delete_knowledge_entry(kid)
    assert project.get_knowledge_entries() == []


def test_search_cache(project):
    cid = project.cache_search_result("大理住宿", {"status": 0, "data": [1, 2]},
                                      source_tool="search_hotel")
    cached = project.get_cached_searches()
    assert any(c["id"] == cid for c in cached)
    full = project.get_search_result_by_id(cid)
    assert full["query"] == "大理住宿"
    assert full["source_tool"] == "search_hotel"
    assert project.get_search_result_by_id("does_not_exist") is None


def test_proposal_latest_prefers_final(project):
    project.save_proposal("# 草稿", version="draft")
    assert "草稿" in project.get_latest_proposal()

    project.save_proposal("# 终稿", version="final")
    assert "终稿" in project.get_latest_proposal()


def test_no_proposal_returns_none(project):
    assert project.get_latest_proposal() is None


def test_chat_overflow_rolls_to_log_file(project):
    for i in range(60):
        project.append_chat("user", f"消息{i}")
    # 上下文窗口截断为 50，溢出写入 chat_history.jsonl
    assert len(project.state["dialogue_context"]) == 50
    assert project.chat_log_file.exists()
    recent = project.get_recent_messages(20)
    assert len(recent) == 20


def test_add_note(project):
    project.add_note("备忘：带录音笔")
    assert project.state["notes"][-1]["content"] == "备忘：带录音笔"
