"""Task 3：9 个状态工具 — repository 接线与 check_advance 组合（统一包络 dict 进出）。

handler 调用风格按控制者裁决 1 统一为 kwargs 具名参数（server 以
``handlers[name](**arguments)`` 调用）；未知 project_id 一律兜底为
``project_not_found`` 错误包络（裁决 2），不崩溃、不泄协议异常。
"""
import pytest
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server.state_tools import STATE_TOOLS, build_handlers
from mcp.types import Tool


def make_repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


def test_state_tools_definitions_cover_nine():
    assert len(STATE_TOOLS) == 9 and all(isinstance(t, Tool) for t in STATE_TOOLS)
    assert {"create_project", "check_phase_advance", "search_knowledge"} \
        <= {t.name for t in STATE_TOOLS}


def test_create_and_get_roundtrip(tmp_path):
    h = build_handlers(make_repo(tmp_path))
    out = h["create_project"](project_id="p1")
    assert out["ok"] and out["data"]["project_id"] == "p1"
    got = h["get_project"](project_id="p1")
    assert got["data"]["phase"] == "research_positioning"


def test_update_context_and_advance(tmp_path):
    h = build_handlers(make_repo(tmp_path))
    h["create_project"](project_id="p1")
    h["update_research_context"](project_id="p1", patch={
        "discipline": "人类学", "topic": "宗族", "scale": "单人", "budget": "2万"})
    r = h["check_phase_advance"](project_id="p1")
    assert r["ok"] and r["data"]["can_advance"] is True
    assert r["data"]["to_phase"] == "field_site_profiles"


def test_unknown_project_is_error_not_crash(tmp_path):
    h = build_handlers(make_repo(tmp_path))
    out = h["get_project"](project_id="ghost")
    assert out["ok"] is False and out["error"]["code"] == "project_not_found"


def test_save_site_and_informant(tmp_path):
    repo = make_repo(tmp_path)
    h = build_handlers(repo)
    h["create_project"](project_id="p1")
    repo.set_phase("p1", "field_site_profiles")   # 宿主落地 A→B，场景与注释一致（B→C 判定）
    assert h["save_field_site"](project_id="p1",
                                record={"name": "喜洲", "confirmed": True})["ok"]
    assert h["save_informant"](project_id="p1",
                               record={"code": "P1", "type": "长老"})["ok"]
    r = h["check_phase_advance"](project_id="p1")
    assert r["data"]["can_advance"] is True   # confirmed site + ≥1 informant → B→C 由宿主 set_phase 落地


def test_knowledge_add_search(tmp_path):
    h = build_handlers(make_repo(tmp_path))
    h["create_project"](project_id="p1")
    h["add_knowledge_entry"](project_id="p1", title="族谱材料",
                             content="明代以降宗族碑刻著录", tags=["碑刻"])
    hits = h["search_knowledge"](project_id="p1", text="碑刻")
    assert hits["ok"] and len(hits["data"]) == 1


@pytest.mark.parametrize(("name", "kwargs"), [
    ("get_project", {"project_id": "ghost"}),
    ("update_research_context", {"project_id": "ghost", "patch": {"topic": "x"}}),
    ("save_field_site", {"project_id": "ghost", "record": {"name": "x"}}),
    ("save_informant", {"project_id": "ghost", "record": {"code": "P1"}}),
    ("add_knowledge_entry", {"project_id": "ghost", "title": "t", "content": "c"}),
    ("search_knowledge", {"project_id": "ghost", "text": "x"}),
    ("check_phase_advance", {"project_id": "ghost"}),
])
def test_unknown_project_wrapped_everywhere(tmp_path, name, kwargs):
    """裁决 2：凡收 project_id 的 handler 均兜 KeyError → project_not_found，逐一不遗漏。"""
    h = build_handlers(make_repo(tmp_path))
    out = h[name](**kwargs)
    assert out["ok"] is False and out["error"]["code"] == "project_not_found"
