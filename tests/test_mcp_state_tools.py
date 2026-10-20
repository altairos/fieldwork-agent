"""Task 3：9 个状态工具 — repository 接线与 check_advance 组合（统一包络 dict 进出）。

handler 调用风格按控制者裁决 1 统一为 kwargs 具名参数（server 以
``handlers[name](**arguments)`` 调用）；未知 project_id 一律兜底为
``project_not_found`` 错误包络（裁决 2），不崩溃、不泄协议异常。

终审 C1 增补：project_id 客户端可控且被 JsonRepository 直拼文件系统路径——
8 个收 project_id 的 handler 必须先过净化守卫，穿越输入折 ``invalid_args``
包络，且 projects 根之外不得出现任何新目录。
"""
import pytest
from fieldwork_agent.kernel.context_patch import ALLOWED_CONTEXT_FIELDS
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server import request_ctx
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
        "purpose": "kaiti", "discipline": "人类学", "topic": "宗族", "scale": "单人",
        "budget": "2万", "time_window": "7月", "research_questions": "宗族礼生角色"})
    r = h["check_phase_advance"](project_id="p1")
    assert r["ok"] and r["data"]["can_advance"] is True
    assert r["data"]["to_phase"] == "field_site_profiles"


def test_academic_context_and_clear_use_shared_contract(tmp_path):
    h = build_handlers(make_repo(tmp_path))
    h["create_project"](project_id="p1")
    result = h["update_research_context"](project_id="p1", patch={
        "research_orientation": "华南学派", "target_institution": "中大", "target_advisor": "陈老师",
    })
    assert result["ok"]
    result = h["update_research_context"](project_id="p1", patch={"clear_fields": ["target_advisor"]})
    assert result["ok"]
    assert result["data"]["target_advisor"] == ""
    assert result["data"]["target_institution"] == "中大"
    assert "clear_fields" not in result["data"]


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


def test_double_dot_inside_pid_now_accepted(tmp_path):
    """审计 F01 语义修正：整值匹配 . / ..，合法名含子串 .. 不再误拒。"""
    h = build_handlers(make_repo(tmp_path))
    out = h["create_project"](project_id="study..2024")
    assert out["ok"] is True
    assert out["data"]["project_id"] == "study..2024"


def test_r05_update_context_rejects_bad_patch(tmp_path):
    """审计二 R05：MCP 入口非法 patch → invalid_args 包络（此前直通浅合并）。"""
    h = build_handlers(make_repo(tmp_path))
    h["create_project"](project_id="p1")
    r = h["update_research_context"](project_id="p1", patch={"budget": 5})
    assert r["ok"] is False
    assert r["error"]["code"] == "invalid_args"
    assert "budget 须为字符串" in r["error"]["message"]
    assert "白名单" in r["error"]["hint"]


def test_r09_stale_confirmation_blocks_advance(tmp_path):
    """审计二 R09：确认失效（五路指纹变化）后 check_phase_advance 不再放行 D→E。"""
    repo = make_repo(tmp_path)
    h = build_handlers(repo)
    h["create_project"](project_id="p1")
    repo.set_phase("p1", "proposal_generation")
    did = repo.save_proposal("p1", "# 方案", version="final")
    repo.confirm_proposal("p1", did)
    r1 = h["check_phase_advance"](project_id="p1")
    assert r1["ok"] and r1["data"]["can_advance"] is True
    assert r1["data"]["to_phase"] == "finished"
    # 研究上下文变化 → 指纹不一致 → 确认失效 → 阻断推进（旧行为：仍放行）
    repo.update_research_context(
        "p1", {**repo.get_research_context("p1"), "budget": "9万"})
    r2 = h["check_phase_advance"](project_id="p1")
    assert r2["ok"]
    assert r2["data"]["can_advance"] is False
    assert "proposal_confirmation" in r2["data"]["missing_fields"]


def test_update_context_tool_description_lists_full_whitelist():
    """审计三 Fix 3：update_research_context 的 description/patch 描述列全白名单 + 持久化纪律。

    动机：症状①——用户已明确目的地/时长，agent 只在回复中口头复述、不落盘，
    后续轮次重复追问。description 是百炼编排端规划调用的主要信号源，
    字段举例不全（旧文只列 4 个）+ 无持久化纪律 → 模型漏写。
    字段列表单一来源：ALLOWED_CONTEXT_FIELDS（context_fields_hint 拼装）。
    """
    tool = next(t for t in STATE_TOOLS if t.name == "update_research_context")
    desc = tool.description or ""
    patch_desc = tool.input_schema["properties"]["patch"]["description"]
    for field in ALLOWED_CONTEXT_FIELDS:
        assert field in patch_desc, f"patch 描述缺字段：{field}"
    assert "持久化" in desc, "description 缺持久化纪律文案"


# ---------------------------------------------------------------- 审计三 Fix 4：知识库「存了看得见」


def test_knowledge_tools_advertise_view_path():
    """审计三 Fix 4：CLI/managed agent 场景无 Web 面板，查看途径必须写进工具面。

    症状：agent 称「已存入知识库」但用户无法即时查看。repo 层 search_knowledge
    空文本本就返回全量（json/sqlite 双实现一致），缺的是把这个能力告诉
    agent——description 是百炼编排端规划调用的主要信号源：
    search_knowledge 需声明空串列出全部；add_knowledge_entry 需声明
    写入后应告知用户查看方式。
    """
    search_tool = next(t for t in STATE_TOOLS if t.name == "search_knowledge")
    assert "空字符串" in (search_tool.description or "")
    assert "全部" in (search_tool.description or "")
    assert "空字符串" in search_tool.input_schema["properties"]["text"]["description"]
    add_tool = next(t for t in STATE_TOOLS if t.name == "add_knowledge_entry")
    assert "告知用户" in (add_tool.description or "")
    assert "search_knowledge" in (add_tool.description or "")


def test_add_knowledge_entry_result_has_view_hint(tmp_path):
    """审计三 Fix 4：add 成功包络带 view_hint，即时提醒 agent 在回复中告知查看命令。

    description 是规划期信号，view_hint 是结果期兑底——即使模型没细读
    描述，工具结果内的指引也会随回合进入上下文。
    """
    h = build_handlers(make_repo(tmp_path))
    h["create_project"](project_id="p1")
    r = h["add_knowledge_entry"](project_id="p1", title="t", content="c")
    assert r["ok"] and r["data"]["id"]
    hint = r["data"]["view_hint"]
    assert "search_knowledge" in hint and "空字符串" in hint


def test_search_knowledge_empty_text_lists_all(tmp_path):
    """审计三 Fix 4：text="" 走通 handler 全链路返回全量条目。

    repo 能力（json/sqlite 空文本→list_knowledge）在 MCP 面的直达验证——
    agent 说「列出知识库」时按新描述空串调用，必须真的拿得到全部条目。
    """
    h = build_handlers(make_repo(tmp_path))
    h["create_project"](project_id="p1")
    h["add_knowledge_entry"](project_id="p1", title="族谱", content="碑刻著录")
    h["add_knowledge_entry"](project_id="p1", title="访谈", content="提纲草稿")
    r = h["search_knowledge"](project_id="p1", text="")
    assert r["ok"] and len(r["data"]) == 2


# ---------------------------------------------------------------- N01：MCP 归属强制（单元级）


def test_stdio_mode_sees_all_without_token(tmp_path):
    """enforce_owner=False（stdio 可信全量）：无令牌也能建/列/读，行为不变。"""
    repo = make_repo(tmp_path)
    h = build_handlers(repo)   # 缺省 enforce_owner=False
    assert h["create_project"](project_id="s1")["ok"]
    listed = h["list_projects"]()["data"]
    assert any(p["project_id"] == "s1" for p in listed)
    assert h["get_project"](project_id="s1")["ok"]


def test_enforce_owner_denies_without_token(tmp_path):
    """enforce_owner=True 且无令牌（resolved_owner=None）：建/列/读全部拒绝。"""
    repo = make_repo(tmp_path)
    repo.create("other", owner="anon:alice")   # 预置他人项目
    h = build_handlers(repo, enforce_owner=True)
    tok = request_ctx.set_owner(None)
    try:
        assert h["create_project"](
            project_id="e1")["error"]["code"] == "owner_token_required"
        assert h["list_projects"]()["data"] == []
        out = h["get_project"](project_id="other")
        assert out["ok"] is False and out["error"]["code"] == "project_not_found"
        assert not h["update_research_context"](
            project_id="other", patch={"topic": "x"})["ok"]
    finally:
        request_ctx.reset_owner(tok)


def test_enforce_owner_isolates_between_users(tmp_path):
    """enforce_owner=True：alice 建/读通过（owner 不泄露），bob 读 alice 项目被拒。"""
    repo = make_repo(tmp_path)
    h = build_handlers(repo, enforce_owner=True)
    tok = request_ctx.set_owner("anon:alice")
    try:
        assert h["create_project"](project_id="a1")["ok"]
        got = h["get_project"](project_id="a1")
        assert got["ok"] and "owner" not in got["data"]
        assert [p["project_id"] for p in h["list_projects"]()["data"]] == ["a1"]
    finally:
        request_ctx.reset_owner(tok)
    tok2 = request_ctx.set_owner("anon:bob")
    try:
        out = h["get_project"](project_id="a1")
        assert out["ok"] is False and out["error"]["code"] == "project_not_found"
        assert h["list_projects"]()["data"] == []
        # bob 用 alice 的 pid create → 拒绝（不覆盖）
        assert h["create_project"](
            project_id="a1")["error"]["code"] == "project_not_found"
    finally:
        request_ctx.reset_owner(tok2)
    # 归属未被 bob 篡改：alice 仍可读回
    tok3 = request_ctx.set_owner("anon:alice")
    try:
        assert h["get_project"](project_id="a1")["ok"]
    finally:
        request_ctx.reset_owner(tok3)
