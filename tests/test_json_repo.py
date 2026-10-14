"""JsonRepository 单测 — 继承 L2 契约套件 + Json 文件实现专属断言（含存量兼容）。"""

import json
import re
import shutil
from pathlib import Path

import pytest
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.protocol import ProjectRepository

# TestContract 靠 pytest 在收集期解析；repo/repo_factory 必须一并导入，
# 否则契约用例找不到 fixture（这两个名字在本模块代码里不直接使用）。
from repository_contract import TestContract, repo, repo_factory  # noqa: F401

LEGACY_FIXTURE = Path(__file__).parent / "fixtures" / "legacy_project"


@pytest.fixture
def json_repo(tmp_path):
    """Json 专属用例用的项目仓库（与契约夹具同样以幂等 create 打开 p1）。"""
    r = JsonRepository(projects_dir=tmp_path / "projects")
    r.create("p1")
    return r


@pytest.fixture
def legacy_repo(tmp_path):
    """把 fixture 里的旧版项目目录拷进 tmp_path 后"打开"（create 幂等 = 存量规范化）。"""
    projects = tmp_path / "projects"
    shutil.copytree(LEGACY_FIXTURE, projects / "legacy_project")
    r = JsonRepository(projects_dir=projects)
    r.create("legacy_project")
    return r


def _read_state(repo_obj, pid):
    path = repo_obj.root / pid / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 协议与目录布局


def test_satisfies_protocol(json_repo):
    assert isinstance(json_repo, ProjectRepository)


def test_root_is_projects_dir(tmp_path):
    r = JsonRepository(projects_dir=tmp_path / "projects")
    assert r.root == tmp_path / "projects"


def test_create_builds_legacy_layout(json_repo):
    json_repo.create("p1")  # 幂等：重复 create 不清空存量
    pdir = json_repo.root / "p1"
    assert (pdir / "state.json").exists()
    for sub in ("knowledge", "search_cache", "output"):
        assert (pdir / sub).is_dir()


def test_state_window_rolls_at_50(json_repo):
    for i in range(60):
        json_repo.append_message("p1", "user", f"m{i}")
    state = _read_state(json_repo, "p1")
    assert len(state["dialogue_context"]) == 50
    assert state["dialogue_context"][0]["content"] == "m10"
    # 全量史在 jsonl：50 只是窗口副本，不是丢失
    lines = (json_repo.root / "p1" / "chat_history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 60


def test_state_json_is_readable_unicode(json_repo):
    json_repo.save_field_site("p1", {"name": "喜洲"})
    raw = (json_repo.root / "p1" / "state.json").read_text(encoding="utf-8")
    assert "喜洲" in raw  # ensure_ascii=False，与旧版落盘风格一致


def test_proposal_filename_pattern(json_repo):
    json_repo.save_proposal("p1", "# 方案", kind="proposal", version="draft")
    names = [p.name for p in (json_repo.root / "p1" / "output").iterdir()]
    assert len(names) == 1
    assert re.fullmatch(r"proposal_draft_\d{8}_\d{6}(_[0-9a-f]{8})?\.md", names[0])


def test_cache_index_derived_from_files(json_repo):
    """缓存清单以文件为唯一真源（删文件即从清单消失，不靠 state 索引双轨）。"""
    cid = json_repo.cache_search("p1", "q", "search_ai", {"a": 1})
    (json_repo.root / "p1" / "search_cache" / f"{cid}.json").unlink()
    assert json_repo.list_cached_searches("p1") == []


def test_get_cached_search_spans_projects(json_repo):
    """get_cached_search 签名不含 pid，故按全局唯一 id 跨项目定位。"""
    json_repo.create("p2")
    cid = json_repo.cache_search("p2", "q2", "search_ai", {"a": 1})
    assert json_repo.get_cached_search(cid)["query"] == "q2"


def test_delete_unknown_knowledge_is_noop(json_repo):
    json_repo.delete_knowledge("p1", "no_such_entry")
    assert json_repo.list_knowledge("p1") == []


# ---------------------------------------------------------------- 存量兼容（Step 5）


def test_legacy_state_keys_complete(legacy_repo):
    state = legacy_repo.get_state("legacy_project")
    # 旧键原样保留
    for key in ("researcher_info", "travel_plan", "proposal_draft", "final_proposal",
                "search_results", "notes"):
        assert key in state, f"旧状态键 {key} 丢失"
    assert state["phase"] == "field_site_profiles"
    assert state["researcher_info"]["research_discipline"] == "人类学"
    # 新键补齐
    for key in ("research_context", "informants", "proposals", "phase_history"):
        assert key in state, f"新增状态键 {key} 未补齐"
    assert state["informants"] == []
    assert state["phase_history"][-1]["to"] == "field_site_profiles"


def test_legacy_proposal_draft_readable(legacy_repo):
    """旧版只有 proposal_draft 单值键，也必须被 list_proposals / 最新读出兼容取出。"""
    docs = legacy_repo.list_proposals("legacy_project")
    assert len(docs) == 1
    assert docs[0]["version"] == "draft"
    assert docs[0]["filename"] == "proposal_draft_20260105_101500.md"
    assert "研究定位" in docs[0]["content"]
    latest = legacy_repo.get_latest_proposal("legacy_project")
    assert latest["content"] == docs[0]["content"]
    # 新写入与旧记录共存，旧的排在前面（按 saved_at 升序）
    legacy_repo.save_proposal("legacy_project", "# 新草稿", version="draft")
    docs = legacy_repo.list_proposals("legacy_project")
    assert len(docs) == 2
    assert legacy_repo.get_latest_proposal("legacy_project")["content"] == "# 新草稿"


def test_legacy_knowledge_and_cache_readable(legacy_repo):
    entries = legacy_repo.list_knowledge("legacy_project")
    assert entries[0]["id"] == "20260105095500_0"
    assert entries[0]["category"] == "ethics"
    assert legacy_repo.search_knowledge("legacy_project", "知情同意")

    cached = legacy_repo.list_cached_searches("legacy_project")
    assert cached[0]["id"] == "20260105100000_0"
    assert cached[0]["tool"] == "search_hotel"  # 旧键 source_tool → 读时归一
    full = legacy_repo.get_cached_search("20260105100000_0")
    assert full["result"]["hotel"] == ["大理古城客栈"]


def test_legacy_field_sites_without_id(legacy_repo):
    """旧版田野点记录没有 id，读出保持原样，不强行改写存量。"""
    sites = legacy_repo.list_field_sites("legacy_project")
    assert sites[0]["name"] == "喜洲"
    assert "id" not in sites[0]


def test_legacy_chat_tail_backfilled(legacy_repo):
    """旧版把最近若干条留在 state 窗口、只有溢出进 jsonl；打开后 jsonl 必须补齐全量。"""
    msgs = legacy_repo.get_messages("legacy_project", limit=100)
    assert [m["content"] for m in msgs] == [
        "你好，帮我规划田野调查",
        "先确定研究定位：学科、问题、地点、规模、经费、时间窗口。",
        "人类学，研究白族宗族组织变迁，预算两万，三个月",
        "建议把田野点锁定在喜洲与大理两地。",
    ]
    # 幂等：重开不重复补写
    again = JsonRepository(projects_dir=legacy_repo.root)
    again.create("legacy_project")
    assert len(again.get_messages("legacy_project", limit=100)) == 4
