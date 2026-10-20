"""JsonRepository 单测 — 继承 L2 契约套件 + Json 文件实现专属断言（含存量兼容）。"""

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fieldwork_agent.kernel.repository.confirm_hash import ProposalConfirmError
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


def test_pop_last_message_syncs_dialogue_window(json_repo):
    json_repo.append_message("p1", "user", "提问")
    json_repo.append_message("p1", "assistant", "回答")
    assert [m["role"] for m in _read_state(json_repo, "p1")["dialogue_context"]] == [
        "user", "assistant"]
    json_repo.pop_last_message("p1")
    state = _read_state(json_repo, "p1")
    assert [m["role"] for m in state["dialogue_context"]] == ["user"]
    assert [m["content"] for m in json_repo.get_messages("p1", limit=0)] == ["提问"]


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


def test_cache_index_legacy_record_derives_on_read(json_repo):
    """R12：存量 Json 缓存文件无 empty/truncated 字段 → 索引时从已读入 result 现场派生。"""
    # 直接写一个旧形状文件进 cache 目录（对照 test_cache_index_derived_from_files 的写法）
    payload = {"id": "srch_20260101_000000_legacy0", "query": "q",
               "tool": "search_ai", "cached_at": "2026-01-01T00:00:00",
               "result": {"items": [], "total": 0}}
    entry = json_repo.root / "p1" / "search_cache" / (payload["id"] + ".json")
    entry.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    idx = json_repo.list_cached_searches("p1")[0]
    assert idx["empty"] is True and idx["truncated"] is False and idx["total"] == 0
    assert idx["parent_call_id"] == "" and idx["turn_id"] == "" and idx["query_key"] == ""


def test_r01_get_cached_search_scoped_to_project(json_repo):
    """审计二 R01：get_cached_search 收窄项目域——p1 读不到 p2 的快照。"""
    json_repo.create("p2")
    cid = json_repo.cache_search("p2", "q2", "search_ai", {"a": 1})
    assert json_repo.get_cached_search("p2", cid)["query"] == "q2"
    assert json_repo.get_cached_search("p1", cid) is None
    assert json_repo.get_cached_search("no_such_project", cid) is None


@pytest.mark.parametrize("bad_cid", [
    "../p1/state", "..\\..\\secret", "a/b", "a\\b", "*", "?.json",
    "", "x" * 65, ".json", "spacer.cid",
])
def test_r01_bad_cid_returns_none(json_repo, bad_cid):
    """审计二 R01：cid 白名单外的任何形态一律 None（先于文件系统拼接）。"""
    json_repo.cache_search("p1", "q", "search_ai", {"a": 1})
    assert json_repo.get_cached_search("p1", bad_cid) is None


def test_r01_symlinked_cache_entry_rejected(json_repo, tmp_path):
    """search_cache 内符号链接条目（指向外部敏感文件）不得被读出（防御纵深）。"""
    outside = tmp_path / "secret.json"
    outside.write_text(json.dumps({"query": "leak", "tool": "t", "result": "leak"}),
                       encoding="utf-8")
    cid = json_repo.cache_search("p1", "q", "search_ai", {"a": 1})
    entry = json_repo.root / "p1" / "search_cache" / f"{cid}.json"
    entry.unlink()
    try:
        entry.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台/权限不支持符号链接")
    assert json_repo.get_cached_search("p1", cid) is None


def test_r01_symlinked_cache_dir_rejected(json_repo, tmp_path):
    """审计二 R01（final review F2）：search_cache 目录**自身**外指
    （symlink 到外部目录）时不得越根读取（spec §1.3 受信根包含校验）。"""
    cid = json_repo.cache_search("p1", "q", "search_ai", {"a": 1})
    outside_dir = tmp_path / "outside_cache"
    outside_dir.mkdir()
    (outside_dir / f"{cid}.json").write_text(
        json.dumps({"query": "leak", "tool": "t", "result": "leak"}),
        encoding="utf-8")
    cache_dir = json_repo.root / "p1" / "search_cache"
    shutil.rmtree(cache_dir)
    try:
        cache_dir.symlink_to(outside_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台/权限不支持目录符号链接")
    assert json_repo.get_cached_search("p1", cid) is None


def test_delete_unknown_knowledge_is_noop(json_repo):
    json_repo.delete_knowledge("p1", "no_such_entry")
    assert json_repo.list_knowledge("p1") == []


def test_delete_knowledge_rejects_path_traversal(json_repo):
    """P2-2：entry_id 含路径段（../、分隔符、绝对路径）必须 ValueError，

    防拼路径越 knowledge 目录删任意文件；合法 id 均为单段名（know_...）。
    """
    for evil in ("../../victim", "..\\..\\victim", "/etc/passwd", "C:\\windows\\evil"):
        with pytest.raises(ValueError):
            json_repo.delete_knowledge("p1", evil)


def test_update_knowledge_rejects_path_traversal(json_repo):
    """P2-2 同源：update 与 delete 共用单段文件名守卫，防拼路径越
    knowledge 目录改写任意文件。"""
    for evil in ("../../victim", "..\\..\\victim", "/etc/passwd", "C:\\windows\\evil"):
        with pytest.raises(ValueError):
            json_repo.update_knowledge("p1", evil, content="x")


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
    # 旧记录无指纹也恒有 dep_hash 键（空串=无指纹）：读出形状与 sqlite 侧
    # list_proposals 七键平价，迁移全等校验（test_migrate）依赖此形状。
    assert docs[0]["dep_hash"] == ""
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
    full = legacy_repo.get_cached_search("legacy_project", "20260105100000_0")
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

# ---------------------------------------------------------------- 损坏 state.json 响亮失败 + 原子写


def test_corrupt_state_json_raises_valueerror(tmp_path):
    """半截 state.json 不得被当作空项目静默重置——必须 raise ValueError 且原文件保留。"""
    projects = tmp_path / "projects"
    pid_dir = projects / "p1"
    pid_dir.mkdir(parents=True)
    corrupt = '{"project_id": "p1", "phase": "research_positioning", "phase_hi'
    (pid_dir / "state.json").write_text(corrupt, encoding="utf-8")
    r = JsonRepository(projects_dir=projects)
    # _require 通过（文件存在），但解析失败 -> ValueError
    with pytest.raises(ValueError, match="损坏"):
        r.get_state("p1")
    # 原文件未被覆盖（人工可抢救）
    assert (pid_dir / "state.json").read_text(encoding="utf-8") == corrupt


def test_corrupt_state_json_list_projects_raises(tmp_path):
    """list_projects 遇到损坏 state.json 同样 raise，不产生幻影正常项。"""
    projects = tmp_path / "projects"
    (projects / "good" ).mkdir(parents=True)
    (projects / "good" / "state.json").write_text(
        '{"project_id":"good","created_at":"x","updated_at":"y",'
        '"phase":"a","phase_history":[],"research_context":{},'
        '"dialogue_context":[],"field_sites":[],"informants":[],'
        '"proposals":[],"travel_plan":null,"researcher_info":{},'
        '"proposal_draft":null,"final_proposal":null,"search_results":[],"notes":[]}',
        encoding="utf-8",
    )
    (projects / "bad").mkdir(parents=True)
    (projects / "bad" / "state.json").write_text("{NOT JSON", encoding="utf-8")
    r = JsonRepository(projects_dir=projects)
    with pytest.raises(ValueError, match="损坏"):
        r.list_projects()


def test_create_corrupt_state_json_raises(tmp_path):
    """create 打开已存在但损坏的 state.json 也必须 raise（而非覆盖重置）。"""
    projects = tmp_path / "projects"
    pid_dir = projects / "p1"
    pid_dir.mkdir(parents=True)
    (pid_dir / "state.json").write_text("{{{broken", encoding="utf-8")
    r = JsonRepository(projects_dir=projects)
    with pytest.raises(ValueError, match="损坏"):
        r.create("p1")


def test_atomic_write_produces_valid_json(tmp_path):
    """正常路径：_write_json 原子落盘后 state.json 是完整合法 JSON（无残留 .tmp）。"""
    projects = tmp_path / "projects"
    r = JsonRepository(projects_dir=projects)
    r.create("p1")
    r.set_phase("p1", "field_site_profiles")
    state_path = projects / "p1" / "state.json"
    # 文件是完整 JSON
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["phase"] == "field_site_profiles"
    # 没有残留 tmp 文件（os.replace 后 tmp 不再存在）
    tmp_files = list(state_path.parent.glob("*.tmp"))
    assert tmp_files == []

# ────────────────────────────────────────────────────────────────
# 审计第二批 F04/F13：确认基础设施 + 按 ID 读文档
# ────────────────────────────────────────────────────────────────

def test_confirm_field_site_by_id(json_repo):
    sid = json_repo.save_field_site("p1", {"name": "喜洲", "confirmed": False})
    assert json_repo.confirm_field_site("p1", sid) is True
    assert json_repo.list_field_sites("p1")[0]["confirmed"] is True
    assert json_repo.confirm_field_site("p1", "no_such") is False
    assert json_repo.confirm_field_site("p1", sid) is True  # 幂等


def test_confirm_proposal_records_hash(json_repo):
    json_repo.update_research_context("p1", {"budget": "2万", "time_window": "暑假"})
    did = json_repo.save_proposal("p1", "# 方案", kind="proposal", version="final")
    record = json_repo.confirm_proposal("p1", did)
    assert record["kind"] == "proposal" and record["dep_hash"]
    state = _read_state(json_repo, "p1")
    assert state["proposal_confirmed"] is True
    assert len(state["confirmations"]) == 1
    assert state["confirmations"][0]["dep_hash"] == record["dep_hash"]


def test_confirmation_stale_after_budget_change(json_repo):
    did = json_repo.save_proposal("p1", "# 方案")
    json_repo.confirm_proposal("p1", did)
    assert json_repo.is_proposal_confirmation_stale("p1") is False
    json_repo.update_research_context("p1", {"budget": "3万"})
    assert json_repo.is_proposal_confirmation_stale("p1") is True


def test_confirmation_stale_after_new_proposal(json_repo):
    did = json_repo.save_proposal("p1", "# 方案")
    json_repo.confirm_proposal("p1", did)
    assert json_repo.is_proposal_confirmation_stale("p1") is False
    json_repo.save_proposal("p1", "# 方案 v2", version="final")
    assert json_repo.is_proposal_confirmation_stale("p1") is True


def test_stale_false_without_confirmation(json_repo):
    assert json_repo.is_proposal_confirmation_stale("p1") is False


def test_get_proposal_by_id_roundtrip(json_repo):
    pid = json_repo.save_proposal("p1", "正文A", version="draft")
    json_repo.save_proposal("p1", "正文B", version="draft")
    doc = json_repo.get_proposal_by_id("p1", pid)
    assert doc is not None and doc["content"] == "正文A"
    assert json_repo.get_proposal_by_id("p1", "no_such") is None


# ------------------------------------------------------------ 审计二 R09


def test_r09_confirm_four_way_rejections(json_repo):
    """四重校验：不存在 / 类型错 / 缺指纹 / 指纹不一致。"""
    did = json_repo.save_proposal("p1", "# 方案")
    # ① not_found：确认不存在的文档 id
    with pytest.raises(ProposalConfirmError) as ei:
        json_repo.confirm_proposal("p1", "prop_20990101_000000_deadbeef")
    assert ei.value.code == "not_found"
    # ② wrong_kind：interview_guide 文档不可确认
    gid = json_repo.save_proposal("p1", "# 提纲", kind="interview_guide")
    with pytest.raises(ProposalConfirmError) as ei:
        json_repo.confirm_proposal("p1", gid)
    assert ei.value.code == "wrong_kind"
    # ③ dep_hash_missing：手工抹掉指纹，模拟旧版生成的文档
    state = _read_state(json_repo, "p1")
    for r in state["proposals"]:
        if r["id"] == did:
            r.pop("dep_hash", None)
    (json_repo.root / "p1" / "state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ProposalConfirmError) as ei:
        json_repo.confirm_proposal("p1", did)
    assert ei.value.code == "dep_hash_missing"
    # ④ dep_hash_mismatch：手工写死假指纹，模拟依赖已变化
    state = _read_state(json_repo, "p1")
    for r in state["proposals"]:
        if r["id"] == did:
            r["dep_hash"] = "deadbeef"
    (json_repo.root / "p1" / "state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ProposalConfirmError) as ei:
        json_repo.confirm_proposal("p1", did)
    assert ei.value.code == "dep_hash_mismatch"


def test_r09_site_change_flips_stale(json_repo):
    """五路依赖之②：确认后新增田野点 → 确认失效（旧六字段指纹不含田野点）。"""
    did = json_repo.save_proposal("p1", "# 方案")
    json_repo.confirm_proposal("p1", did)
    assert json_repo.is_proposal_confirmation_stale("p1") is False
    json_repo.save_field_site("p1", {"name": "喜洲", "confirmed": False})
    assert json_repo.is_proposal_confirmation_stale("p1") is True


def test_r09_itinerary_snapshot_change_flips_stale(json_repo):
    """五路依赖之⑤：确认后产生新的 compose_itinerary 快照 → 确认失效。"""
    did = json_repo.save_proposal("p1", "# 方案")
    json_repo.confirm_proposal("p1", did)
    assert json_repo.is_proposal_confirmation_stale("p1") is False
    json_repo.cache_search("p1", "行程编排", "compose_itinerary", {"legs": []})
    assert json_repo.is_proposal_confirmation_stale("p1") is True


def test_r09_legacy_confirmation_without_dep_hash_is_stale(json_repo):
    """legacy 确认记录无 dep_hash：无法判定依赖是否变化，保守视失效。"""
    did = json_repo.save_proposal("p1", "# 方案")
    json_repo.confirm_proposal("p1", did)
    state = _read_state(json_repo, "p1")
    state["confirmations"][-1].pop("dep_hash", None)
    (json_repo.root / "p1" / "state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    assert json_repo.is_proposal_confirmation_stale("p1") is True


def test_r09_guide_after_confirm_not_stale(json_repo):
    """审计二 R09（final review F1）：确认 proposal 后保存 interview_guide
    → stale 保持 False（spec §3 按类型判定：提纲与方案确认作用域分开）。"""
    did = json_repo.save_proposal("p1", "# 方案", version="final")
    json_repo.confirm_proposal("p1", did)
    assert json_repo.is_proposal_confirmation_stale("p1") is False
    json_repo.save_proposal("p1", "# 提纲", kind="interview_guide")
    assert json_repo.is_proposal_confirmation_stale("p1") is False


def test_r09_new_proposal_after_confirm_stale(json_repo):
    """审计二 R09（final review F1）：确认后保存新 proposal → stale True
    （现行为保留；本用例是行为回归锚，RED 阶段即 PASS 属预期）。"""
    did = json_repo.save_proposal("p1", "# 方案 v1", version="draft")
    json_repo.confirm_proposal("p1", did)
    json_repo.save_proposal("p1", "# 方案 v2", version="final")
    assert json_repo.is_proposal_confirmation_stale("p1") is True


# ------------------------------------------------------------ 同刻度保存的顺序稳定性


def test_proposal_latest_stable_within_same_wallclock_tick(json_repo, monkeypatch):
    """同一壁钟 tick 内连续保存（Windows 时钟分辨率 ~15.6ms，CI 偶发两次保存
    落同一 tick），latest 判定必须稳定：saved_at 平局时按保存顺序，不得退化
    为随机 uid 字典序（uid 无单调性，曾致 CI windows 矩阵间歇红）。"""
    import fieldwork_agent.kernel.repository.json_repo as jr

    monkeypatch.setattr(jr, "_now", lambda: "2026-10-16T18:55:00.123456")
    # 后保存者 uid 字典序更小：旧实现按 (saved_at, id) 排序必把 v2 排到前面
    uids = iter(["ffffffff", "00000000"])
    monkeypatch.setattr(
        jr.uuid, "uuid4", lambda: SimpleNamespace(hex=next(uids) + "0" * 24)
    )
    json_repo.save_proposal("p1", "v1", version="draft")
    json_repo.save_proposal("p1", "v2", version="draft")
    docs = json_repo.list_proposals("p1")
    assert [d["content"] for d in docs] == ["v1", "v2"]
    assert json_repo.get_latest_proposal("p1")["content"] == "v2"


def _freeze_same_tick(monkeypatch):
    """把壁钟钉死在同一 tick：_new_id 的时间戳前缀与 _now() 全部固定，且
    后保存者的 uid 字典序更小（旧实现的文件名排序必把后保存的排到前面）。"""
    from datetime import datetime as real_datetime

    import fieldwork_agent.kernel.repository.json_repo as jr

    class FrozenDatetime:
        @staticmethod
        def now():
            return real_datetime(2026, 10, 17, 12, 0, 0)

    monkeypatch.setattr(jr, "datetime", FrozenDatetime)
    monkeypatch.setattr(jr, "_now", lambda: "2026-10-17T12:00:00.123456")
    uids = iter(["ffffffff", "00000000"])
    monkeypatch.setattr(
        jr.uuid, "uuid4", lambda: SimpleNamespace(hex=next(uids) + "0" * 24)
    )


def test_knowledge_order_stable_within_same_wallclock_tick(json_repo, monkeypatch):
    """同一壁钟 tick 内连续入库的两条知识（Windows 时钟分辨率 ~15.6ms），
    list_knowledge 顺序必须稳定为保存顺序：文件名字典序不得退化为随机 uid
    ——它同时是确认指纹第四路（compute_confirm_hash 对列表顺序敏感）。"""
    _freeze_same_tick(monkeypatch)
    json_repo.add_knowledge("p1", "t1", "c1")
    json_repo.add_knowledge("p1", "t2", "c2")
    assert [e["title"] for e in json_repo.list_knowledge("p1")] == ["t1", "t2"]


def test_latest_itinerary_cid_stable_within_same_wallclock_tick(json_repo, monkeypatch):
    """同一壁钟 tick 内连续两次 compose_itinerary 缓存，_latest_itinerary_cid
    必须取到后一次：它喂给确认指纹第五路，同 tick 判定随机会让 dep_hash
    无故翻转（confirm_proposal / is_proposal_confirmation_stale 随机失效）。"""
    _freeze_same_tick(monkeypatch)
    cid1 = json_repo.cache_search("p1", "行程", "compose_itinerary", {"day": 1})
    cid2 = json_repo.cache_search("p1", "行程", "compose_itinerary", {"day": 2})
    assert [c["id"] for c in json_repo.list_cached_searches("p1")] == [cid1, cid2]
    assert json_repo._latest_itinerary_cid("p1") == cid2


# ---------------------------------------------------------------- owner 归属（归属隔离）

def test_create_with_owner_persists(json_repo):
    json_repo.create("p_owned", owner="anon:abc123")
    assert json_repo.get_state("p_owned")["owner"] == "anon:abc123"


def test_create_without_owner_is_unowned(json_repo):
    json_repo.create("p_free")
    assert json_repo.get_state("p_free")["owner"] is None


def test_create_idempotent_keeps_existing_owner(json_repo):
    json_repo.create("p_a", owner="anon:first")
    json_repo.create("p_a", owner="anon:second")   # 幂等打开：不覆盖已有归属
    assert json_repo.get_state("p_a")["owner"] == "anon:first"


def test_create_invalid_owner_raises_valueerror(json_repo):
    with pytest.raises(ValueError):
        json_repo.create("p_bad", owner="anon:has space")
    with pytest.raises(ValueError):
        json_repo.create("p_bad", owner="no_namespace")


def test_list_projects_owner_three_states(tmp_path):
    """三态清单从空仓库起算：json_repo 夹具预建无主 p1，会混进全量与 None 两路。"""
    # 局部仓库实例改名 plain_repo：避免遮蔽 L16 为契约收集而导入的 repo fixture（F811）
    plain_repo = JsonRepository(projects_dir=tmp_path / "projects")
    plain_repo.create("p_mine", owner="anon:me")
    plain_repo.create("p_other", owner="anon:other")
    plain_repo.create("p_free")
    assert [p["project_id"] for p in plain_repo.list_projects()] == [
        "p_free", "p_mine", "p_other"]   # 缺省全量，排序不变
    assert [p["project_id"] for p in plain_repo.list_projects(owner="anon:me")] == ["p_mine"]
    assert [p["project_id"] for p in plain_repo.list_projects(owner=None)] == ["p_free"]


def test_set_owner_claims_and_releases(json_repo):
    json_repo.create("p_claim")
    json_repo.set_owner("p_claim", "anon:me")
    assert json_repo.get_state("p_claim")["owner"] == "anon:me"
    json_repo.set_owner("p_claim", "anon:me")   # 幂等：同值直接返回
    json_repo.set_owner("p_claim", None)        # 退回无主
    assert json_repo.get_state("p_claim")["owner"] is None


def test_set_owner_unknown_pid_and_invalid_value(json_repo):
    with pytest.raises(KeyError):
        json_repo.set_owner("nope", "anon:me")
    json_repo.create("p_x")
    with pytest.raises(ValueError):
        json_repo.set_owner("p_x", "Anon:UPPER")


def test_owner_key_backfilled_on_legacy_project(json_repo):
    """存量项目（无 owner 键）打开后 owner 为 None（_load setdefault 补键）。"""
    json_repo.create("p_legacy")
    state_file = json_repo.root / "p_legacy" / "state.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data.pop("owner")
    state_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert json_repo.get_state("p_legacy")["owner"] is None


# ---------------------------------------------------------------- A10 仓储层 ID 校验


def test_unsafe_project_id_writes_nothing_outside_root(tmp_path):
    """A10：绕过外壳直呼仓储，非法 ID 也不得在根目录外留下目录/锁/状态文件。"""
    bare = JsonRepository(projects_dir=tmp_path / "projects")
    with pytest.raises(ValueError):
        bare.create("../escape")
    with pytest.raises(ValueError):
        bare.append_message("../escape", "user", "x")
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "state.json").exists()
    assert not (tmp_path / "escape.lock").exists()
    assert not (tmp_path / "projects").exists()
