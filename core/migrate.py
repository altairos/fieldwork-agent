"""JSON → SQLite 存量搬迁（spec §5.4 里程碑3）。幂等；JSON 目录原地保留作回滚。"""
from __future__ import annotations

import hashlib
import json

from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository


def _hash(value) -> str:
    """抽样字段一致性哈希（canonical JSON → sha256）。"""
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _already_migrated(sqlite_repo: SqliteRepository, pid: str) -> bool:
    """项目级 schema_meta 标记（幂等主语义）：命中 → 整项目跳过，不产生重复行。"""
    with sqlite_repo._connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM schema_meta WHERE key=?", (f"migrated:{pid}",)).fetchone()
    return row is not None


def _mark_project(sqlite_repo: SqliteRepository, pid: str) -> None:
    """单项目迁移完成标记（key=migrated:<pid>）。"""
    with sqlite_repo._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?,?)",
            (f"migrated:{pid}", "json"))


def _mark_migrated(sqlite_repo: SqliteRepository) -> None:
    """全局标记：库的存量来自 JSON（CLI/排障可查）。"""
    with sqlite_repo._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?,?)",
            ("migrated_from", "json"))


def _verify(json_repo: JsonRepository, sqlite_repo: SqliteRepository,
            pids: list[str]) -> None:
    """逐项目比对行数 + 抽样字段哈希（各类首条整条）；不一致抛 RuntimeError。"""
    problems: list[str] = []
    for pid in pids:
        jstate = json_repo.get_state(pid)
        if sqlite_repo.get_phase(pid) != jstate["phase"]:
            problems.append(f"{pid}: phase 不一致")
        pairs = [
            ("messages", json_repo.get_messages(pid, 0), sqlite_repo.get_messages(pid, 0)),
            ("field_sites", jstate["field_sites"], sqlite_repo.list_field_sites(pid)),
            ("informants", jstate["informants"], sqlite_repo.list_informants(pid)),
            ("knowledge", json_repo.list_knowledge(pid), sqlite_repo.list_knowledge(pid)),
            ("proposals", json_repo.list_proposals(pid), sqlite_repo.list_proposals(pid)),
            ("search_cache", json_repo.list_cached_searches(pid),
             sqlite_repo.list_cached_searches(pid)),
        ]
        for kind, jlist, slist in pairs:
            if len(jlist) != len(slist):
                problems.append(f"{pid}/{kind}: 行数 json={len(jlist)} sqlite={len(slist)}")
            elif jlist and _hash(jlist[0]) != _hash(slist[0]):
                problems.append(f"{pid}/{kind}: 首条记录不一致")
    if problems:
        raise RuntimeError("迁移校验失败:\n  " + "\n  ".join(problems))


def migrate_to_sqlite(json_repo: JsonRepository, sqlite_repo: SqliteRepository,
                      project_id: str | None = None) -> dict:
    """扫描存量项目 → 逐项目导入 7 类数据 → 行数 + 抽样字段哈希校验 → 幂等标记。

    - 幂等主语义：每项目迁移完成写 schema_meta 项目级标记（``migrated:<pid>``），
      再迁命中即整项目跳过；记录表的 ``INSERT OR REPLACE`` 仅作中断重跑的兜底；
      标记在校验通过后写入，失败重迁自愈（未被标记的项目下轮重新导入）；
    - 迁移先经 ``json_repo.create`` 幂等打开：触发 json 侧存量消息史规范化
      （窗口独有消息补写进 jsonl），否则旧版项目最近对话静默丢失；
    - messages 自增主键无稳定 id：导入前先清空该项目存量再整段重导；
    - 记录类一律走 raw-import（``_import_*``）保留原 id/时间戳/项目时间戳，
      公开写方法会以 ``_now()`` 重盖时间戳或生成新 id，破坏与 JSON 侧的全等；
    - JSON 目录原地保留（天然回滚：不设 ``FA_STORAGE`` 即回 JSON 只读使用）。
    """
    pids = [project_id] if project_id else [p["project_id"] for p in json_repo.list_projects()]
    report = {"projects": 0, "messages": 0, "field_sites": 0, "informants": 0,
              "search_cache": 0, "knowledge": 0, "proposals": 0, "skipped": 0}
    migrated: list[str] = []   # 本轮实际导入的 pid；_verify 通过后才落标记
    for pid in pids:
        if _already_migrated(sqlite_repo, pid):
            report["skipped"] += 1
            continue
        jstate = json_repo.get_state(pid)
        json_repo.create(pid)   # 幂等打开 = 触发 json 侧存量消息史规范化（backfill）
        sqlite_repo.create(pid)                              # 幂等建项目
        sqlite_repo.set_phase(pid, jstate["phase"])          # 阶段（同阶段不写史）
        # phase_history 原样搬迁（set_phase 只记本次转移，存量历史需 raw 写入）
        sqlite_repo._import_phase_history(pid, jstate["phase_history"])
        if jstate["research_context"]:
            sqlite_repo.update_research_context(pid, jstate["research_context"])
        # 项目时间戳保形：raw 回写原 created_at/updated_at（公开 create/set_phase/
        # update_research_context 均会以 _now() 重盖，故置于这些调用之后）
        sqlite_repo._import_project_meta(pid, jstate["created_at"], jstate["updated_at"])
        sqlite_repo._clear_messages(pid)                     # 无稳定主键：清空重导
        for m in json_repo.get_messages(pid, limit=0):       # 全量消息，保留原 ts
            sqlite_repo._import_message(pid, m["role"], m["content"],
                                        m.get("metadata", {}), m["ts"])
            report["messages"] += 1
        for s in jstate["field_sites"]:
            sqlite_repo._import_record("field_sites", pid, s)
            report["field_sites"] += 1
        for i in jstate["informants"]:
            sqlite_repo._import_record("informants", pid, i)
            report["informants"] += 1
        for c in json_repo.list_cached_searches(pid):        # 缓存（取全量含 result）
            full = json_repo.get_cached_search(pid, c["id"])
            if full is not None:
                sqlite_repo._import_cache(pid, full)
                report["search_cache"] += 1
        for k in json_repo.list_knowledge(pid):
            sqlite_repo._import_knowledge(pid, k)
            report["knowledge"] += 1
        for p in json_repo.list_proposals(pid):              # 含旧单值键兼容读出
            sqlite_repo._import_proposal(pid, p)
            report["proposals"] += 1
        migrated.append(pid)
        report["projects"] += 1
    _verify(json_repo, sqlite_repo, pids)   # 行数 + 抽样哈希；不一致抛 RuntimeError
    for pid in migrated:                    # 校验通过才标记（失败重迁自愈）
        _mark_project(sqlite_repo, pid)
    _mark_migrated(sqlite_repo)
    return report
