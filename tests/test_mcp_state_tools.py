"""Task 3：9 个状态工具 — repository 接线与 check_advance 组合（统一包络 dict 进出）。

handler 调用风格按控制者裁决 1 统一为 kwargs 具名参数（server 以
``handlers[name](**arguments)`` 调用）；未知 project_id 一律兜底为
``project_not_found`` 错误包络（裁决 2），不崩溃、不泄协议异常。

终审 C1 增补：project_id 客户端可控且被 JsonRepository 直拼文件系统路径——
8 个收 project_id 的 handler 必须先过净化守卫，穿越输入折 ``invalid_args``
包络，且 projects 根之外不得出现任何新目录。
"""
import inspect

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


# ---------------------------------------------------------------- 终审⑦⑧⑨：定义 ↔ handler 防漂移


def test_tool_names_match_handler_keys(tmp_path):
    """终审⑦①：STATE_TOOLS 与 build_handlers 的键集全等（少一个即 tools/call 黑洞）。"""
    handlers = build_handlers(make_repo(tmp_path))
    assert {t.name for t in STATE_TOOLS} == set(handlers)


def test_input_schema_matches_handler_signature(tmp_path):
    """终审⑦②：required = 签名中无默认值的参数集；properties = 全部参数名（逐一对照）。

    ``create_project`` 的 ``project_id`` 有默认值 → 只进 properties、不进 required；
    任一侧加/删参数而另一侧忘了跟，本断言当场报出工具名。
    """
    handlers = build_handlers(make_repo(tmp_path))
    for tool in STATE_TOOLS:
        params = inspect.signature(handlers[tool.name]).parameters
        schema = tool.input_schema
        required = {n for n, p in params.items() if p.default is inspect.Parameter.empty}
        assert set(schema.get("required", [])) == required, tool.name
        assert set(schema["properties"]) == set(params), tool.name


def test_create_project_without_id_generates_and_reads_back(tmp_path):
    """终审⑧：缺省 project_id 分支——自动生成 ``field_<时间戳>`` 且能用该 id 读回状态。"""
    h = build_handlers(make_repo(tmp_path))
    out = h["create_project"]()          # 不传 project_id
    assert out["ok"] is True
    pid = out["data"]["project_id"]
    assert pid.startswith("field_")
    got = h["get_project"](project_id=pid)
    assert got["ok"] is True and got["data"]["phase"] == "research_positioning"


def test_project_id_subschema_not_aliased_across_tools():
    """终审⑨：project_id 子 schema 不得是同一 dict 对象。

    pydantic 不拷贝 inputSchema 里的嵌套 dict（实测 ``Tool`` 持有原对象），共享常量时
    给某一个工具定制描述会静默串改其余工具；工厂化后各持一份、内容不变。
    """
    subs = [t.input_schema["properties"]["project_id"]
            for t in STATE_TOOLS if "project_id" in t.input_schema["properties"]]
    assert len(subs) == 8                            # 除 list_projects 外 8 个工具都收 project_id
    assert len({id(s) for s in subs}) == len(subs)   # 8 份互不相同的对象
    assert all(s["type"] == "string" for s in subs)
    # 内容仍与重构前一致：7 个共用默认描述，create_project 自带"缺省自动生成"说明
    assert sum(s["description"].endswith("project_not_found") for s in subs) == 7


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


# ---------------------------------------------------------------- 终审 L：.. 改路径段级判定


def test_dotted_but_legal_pid_accepted(tmp_path):
    """终审 L：``".." in pid`` 子串匹配过宽——``study..2024`` 是合法 id，须可 create + get。"""
    h = build_handlers(make_repo(tmp_path))
    out = h["create_project"](project_id="study..2024")
    assert out["ok"] is True and out["data"]["project_id"] == "study..2024"
    got = h["get_project"](project_id="study..2024")
    assert got["ok"] is True and got["data"]["phase"] == "research_positioning"


@pytest.mark.parametrize("pid", ["../evil", "..\\..\\evil", "..", "C:\\evil", "/etc/passwd"])
def test_traversal_variants_still_rejected_by_segment_rule(tmp_path, pid):
    """终审 L 不得削弱守卫：穿越/裸 ``..``/盘符/绝对路径仍 invalid_args，根外无新目录。"""
    h = build_handlers(make_repo(tmp_path))
    out = h["create_project"](project_id=pid)
    assert out["ok"] is False and out["error"]["code"] == "invalid_args", pid
    assert out["error"]["hint"], pid
    assert not list(tmp_path.rglob("*evil*"))
    assert not (tmp_path.parent / "evil").exists()
    assert not (tmp_path / "etc").exists()


# ---------------------------------------------------------------- 收尾：段级规则拒 "." 与空段


@pytest.mark.parametrize("pid", [".", "./x", "a//b", "a\\b", ".\\x", ".."])
def test_dot_and_empty_segment_pid_rejected(tmp_path, pid):
    """``"."`` 曾通过校验（root/"." 解析回根本身，containment 也放行），把 state.json 与
    knowledge/search_cache/output 直接写进 projects 根，污染根并干扰 list_projects；
    空段（``"a//b"``）为退化形。段级规则须一律拒绝，且 projects 根内外无任何新目录/文件。
    """
    h = build_handlers(make_repo(tmp_path))
    projects_root = tmp_path / "projects"
    out = h["create_project"](project_id=pid)
    assert out["ok"] is False and out["error"]["code"] == "invalid_args", pid
    assert out["error"]["hint"], pid
    # 守卫在任何写入之前拦截：projects 根要么不存在，要么空；根内外均无 state.json
    assert not projects_root.exists() or not any(projects_root.iterdir()), pid
    assert not list(tmp_path.rglob("state.json")), pid


@pytest.mark.parametrize("pid", ["study..2024", "p-1_2", "field_20261015_120000"])
def test_new_segment_rule_keeps_legal_shapes_accepted(tmp_path, pid):
    """新段级规则不误伤：``study..2024``（整体一段而非空段）、``p-1_2``、
    ``field_<时间戳>`` 形状均可正常 create + get。"""
    h = build_handlers(make_repo(tmp_path))
    out = h["create_project"](project_id=pid)
    assert out["ok"] is True and out["data"]["project_id"] == pid, pid
    got = h["get_project"](project_id=pid)
    assert got["ok"] is True and got["data"]["phase"] == "research_positioning", pid
