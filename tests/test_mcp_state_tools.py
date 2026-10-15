"""Task 3：9 个状态工具 — repository 接线与 check_advance 组合（统一包络 dict 进出）。

handler 调用风格按控制者裁决 1 统一为 kwargs 具名参数（server 以
``handlers[name](**arguments)`` 调用）；未知 project_id 一律兜底为
``project_not_found`` 错误包络（裁决 2），不崩溃、不泄协议异常。

终审 C1 增补：project_id 客户端可控且被 JsonRepository 直拼文件系统路径——
8 个收 project_id 的 handler 必须先过净化守卫，穿越输入折 ``invalid_args``
包络，且 projects 根之外不得出现任何新目录。
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
                               record={"code": "P1", "type": "村干部"})["ok"]
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


# ---------------------------------------------------------------- 终审 C1：project_id 穿越净化


def test_relative_traversal_pid_rejected_and_nothing_created(tmp_path):
    """已证实攻击①：``create_project(project_id="../../evil_rel")`` 在 projects 根之外建目录。

    净化守卫必须折成 invalid_args 包络（不抛协议异常），且 tmp_path 内外均无新目录。
    """
    repo = make_repo(tmp_path)
    h = build_handlers(repo)
    out = h["create_project"](project_id="../../evil_rel")
    assert out["ok"] is False and out["error"]["code"] == "invalid_args"
    assert out["error"]["hint"]
    # projects 根 = tmp_path/"projects"，"../../evil_rel" 若落地应在 tmp_path.parent/evil_rel
    assert not (tmp_path.parent / "evil_rel").exists()
    assert not list(tmp_path.rglob("*evil*"))


def test_absolute_pid_rejected_and_nothing_created(tmp_path):
    """已证实攻击②：Windows 绝对路径整体替换基路径（``root / pid`` 的语义陷阱）。"""
    repo = make_repo(tmp_path)
    h = build_handlers(repo)
    target = tmp_path / "evil_abs"
    out = h["create_project"](project_id=str(target))
    assert out["ok"] is False and out["error"]["code"] == "invalid_args"
    assert not target.exists()


@pytest.mark.parametrize(("name", "kwargs"), [
    ("create_project", {"project_id": "../evil_rel"}),
    ("get_project", {"project_id": "../evil_rel"}),
    ("update_research_context", {"project_id": "../evil_rel", "patch": {"topic": "x"}}),
    ("save_field_site", {"project_id": "../evil_rel", "record": {"name": "x"}}),
    ("save_informant", {"project_id": "../evil_rel", "record": {"code": "P1"}}),
    ("add_knowledge_entry", {"project_id": "../evil_rel", "title": "t", "content": "c"}),
    ("search_knowledge", {"project_id": "../evil_rel", "text": "x"}),
    ("check_phase_advance", {"project_id": "../evil_rel"}),
])
def test_traversal_pid_rejected_by_all_handlers(tmp_path, name, kwargs):
    """8 个收 project_id 的 handler 全部走同一净化守卫，逐一不遗漏。"""
    h = build_handlers(make_repo(tmp_path))
    out = h[name](**kwargs)
    assert out["ok"] is False and out["error"]["code"] == "invalid_args"
    # "../evil_rel" 若落地应在 tmp_path/evil_rel（projects 根之外、tmp_path 之内）
    assert not (tmp_path / "evil_rel").exists()


def test_backslash_and_drive_letter_pid_rejected(tmp_path):
    """反斜杠分隔与盘符模式（含盘符相对路径 ``C:evil``）同样拒绝——resources 守卫同源。"""
    h = build_handlers(make_repo(tmp_path))
    for pid in ("..\\..\\evil", "sub\\dir", "C:evil"):
        out = h["create_project"](project_id=pid)
        assert out["ok"] is False and out["error"]["code"] == "invalid_args", pid
    assert not list(tmp_path.rglob("*evil*"))


def test_legal_pid_still_accepted(tmp_path):
    """守卫不误伤：字母数字下划线连字符 + 自动生成的 field_<时间戳> 均可正常建项目。"""
    h = build_handlers(make_repo(tmp_path))
    assert h["create_project"](project_id="p-1_2")["ok"]
    auto = h["create_project"]()
    assert auto["ok"] and auto["data"]["project_id"].startswith("field_")
