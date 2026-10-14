# tests/test_mcp_resources_prompts.py
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server import prompts, resources


def make_repo(tmp_path):
    r = JsonRepository(projects_dir=tmp_path / "projects")
    r.create("p1")
    r.update_research_context("p1", {"topic": "宗族变迁", "purpose": "global"})
    r.save_field_site("p1", {"name": "喜洲", "confirmed": True})
    return r


def test_static_resource_catalog():
    uris = {r.uri for r in resources.STATIC_RESOURCES}
    assert "fieldwork://guidance/phases" in uris
    assert "fieldwork://guidance/questions/research_positioning" in uris
    assert "fieldwork://guidance/checklists/itinerary_planning" in uris
    assert "fieldwork://guidance/informants/library" in uris
    assert "fieldwork://guidance/interview-guide/methodology" in uris
    assert "fieldwork://templates/proposal-outline" in uris
    # 示范词条按库展开
    assert "fieldwork://guidance/informants/长老" in uris


def test_read_phases_resource_mentions_advance(tmp_path):
    out = resources.read_resource(make_repo(tmp_path), "fieldwork://guidance/phases")
    assert out["ok"] and "proposal_confirmation" in out["text"]  # 三态契约随协议分发


def test_project_resources_expand_with_repo(tmp_path):
    repo = make_repo(tmp_path)
    items = resources.list_resources(repo)
    uris = {r.uri for r in items}
    # 项目族三件套（spec §2.2 的 10 类中占 3 类，按存量展开）
    assert {"fieldwork://project/p1/summary", "fieldwork://project/p1/sites",
            "fieldwork://project/p1/informants"} <= uris
    got = resources.read_resource(repo, "fieldwork://project/p1/summary")
    assert got["ok"] and "宗族变迁" in got["text"]
    bad = resources.read_resource(repo, "fieldwork://project/ghost/summary")
    assert bad["ok"] is False and bad["error"]["code"] == "project_not_found"


def test_informants_library_resource_has_examples(tmp_path):
    out = resources.read_resource(make_repo(tmp_path),
                                  "fieldwork://guidance/informants/library")
    assert out["ok"] and "参考起点" in out["text"]   # 明示"非枚举白名单"


def test_prompts_catalog():
    names = {p.name for p in prompts.list_prompts()}
    assert names == {"fieldwork_interview", "refine_proposal_section"}
    got = prompts.get_prompt("fieldwork_interview", {"phase": "field_site_profiles"})
    # 终审 I1：原断言 `A and B or C` 因 and/or 优先级使 C 支恒真（description 字面含
    # "引导"），零验证力——按控制者裁决收紧为「正文确实携带该阶段提示词」
    assert got["ok"] and "田野点画像" in got["messages"][0]["content"]


def test_project_uri_backslash_traversal_rejected(tmp_path):
    """终审 C1：反斜杠穿越 URI 绕过 split("/") 守卫——共用净化函数后必须折 invalid_args。

    `..\\..\\evil` 若直达 repo，会被 JsonRepository 以 root / pid 拼到 projects 根之外；
    断言包络形状 + projects 根之外（tmp_path 内外）均无新目录。
    """
    repo = make_repo(tmp_path)
    out = resources.read_resource(repo, "fieldwork://project/..\\..\\evil/summary")
    assert out["ok"] is False and out["error"]["code"] == "invalid_args"
    assert out["error"]["hint"]
    assert not list(tmp_path.rglob("*evil*"))
    assert not (tmp_path.parent / "evil").exists()
