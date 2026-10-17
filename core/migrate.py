"""JSON → SQLite 存量搬迁（spec §5.4 里程碑3）。幂等；JSON 目录原地保留作回滚。"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

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


def _project_exists(sqlite_repo: SqliteRepository, pid: str) -> bool:
    """sqlite 侧是否已有同名项目行（与迁移标记相互独立：原生写入也会有行）。"""
    with sqlite_repo._connect() as conn:
        return conn.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (pid,)).fetchone() is not None


def _clear_project(sqlite_repo: SqliteRepository, pid: str) -> None:
    """force 重迁前整项目清空：删 `projects` 行即经 FK CASCADE 带走全部子表
    （`_connect` 每连接 `PRAGMA foreign_keys=ON`），另清掉项目级迁移标记。
    """
    with sqlite_repo._connect() as conn:
        conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM schema_meta WHERE key=?", (f"migrated:{pid}",))


def backup(sqlite_repo: SqliteRepository, keep: int = 3) -> Path | None:
    """滚动备份库文件：``<db>.bak.1`` 最新，旧的后移，超出 ``keep`` 丢弃。

    用 sqlite3 Backup API（而非 `shutil.copy`）：WAL 模式下 `-wal`/`-shm` 旁文件
    不必一起搬，备份得到的是一个自洽、已合并 WAL 的库快照。
    库文件不存在（首次迁移）→ ``None``，调用方记 0 份备份。
    备份先写 ``.bak.tmp``、成功后才滚动后原子入位 ``bak.1``：中途失败不留半成品，
    且失败轮不老化既有备份链（滚动发生在备份成功之后）。
    """
    src = sqlite_repo.db_path
    if not src.exists():
        return None

    def slot(i: int) -> Path:
        return src.with_name(f"{src.name}.bak.{i}")

    tmp = src.with_name(f"{src.name}.bak.tmp")
    try:
        source = sqlite3.connect(src)
        target = sqlite3.connect(tmp)
        try:
            source.backup(target)
        finally:   # 显式关闭：with 语句只提交不关连接，Windows 下句柄滞留会让
            source.close()   # 后续 os.replace 报 WinError 32
            target.close()
    except Exception:
        tmp.unlink(missing_ok=True)   # 半成品不落链（终审 T4-M2）
        raise
    for i in range(keep - 1, 0, -1):
        older, newer = slot(i), slot(i + 1)
        if older.exists():
            os.replace(older, newer)
    os.replace(tmp, slot(1))          # 原子入位：bak.1 要么整份、要么没有
    return slot(1)


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
    """逐项目比对行数 + 抽样字段哈希（各类首条整条），含确认面；不一致抛 RuntimeError。"""
    problems: list[str] = []
    for pid in pids:
        jstate = json_repo.get_state(pid)
        sstate = sqlite_repo.get_state(pid)
        if sstate["phase"] != jstate["phase"]:
            problems.append(f"{pid}: phase 不一致")
        if bool(jstate.get("proposal_confirmed")) != bool(sstate.get("proposal_confirmed")):
            problems.append(
                f"{pid}: proposal_confirmed 不一致"
                f" json={bool(jstate.get('proposal_confirmed'))}"
                f" sqlite={bool(sstate.get('proposal_confirmed'))}")
        pairs = [
            ("messages", json_repo.get_messages(pid, 0), sqlite_repo.get_messages(pid, 0)),
            ("field_sites", jstate["field_sites"], sstate["field_sites"]),
            ("informants", jstate["informants"], sstate["informants"]),
            ("knowledge", json_repo.list_knowledge(pid), sqlite_repo.list_knowledge(pid)),
            ("proposals", json_repo.list_proposals(pid), sqlite_repo.list_proposals(pid)),
            ("search_cache", json_repo.list_cached_searches(pid),
             sqlite_repo.list_cached_searches(pid)),
            ("confirmations", jstate.get("confirmations") or [],
             sstate.get("confirmations") or []),
        ]
        for kind, jlist, slist in pairs:
            if len(jlist) != len(slist):
                problems.append(f"{pid}/{kind}: 行数 json={len(jlist)} sqlite={len(slist)}")
            elif jlist and _hash(jlist[0]) != _hash(slist[0]):
                problems.append(f"{pid}/{kind}: 首条记录不一致")
    if problems:
        raise RuntimeError("迁移校验失败:\n  " + "\n  ".join(problems))


def migrate_to_sqlite(json_repo: JsonRepository, sqlite_repo: SqliteRepository,
                      project_id: str | None = None,
                      force: bool = False) -> dict:
    """扫描存量项目 → 逐项目导入 8 类数据 → 行数 + 抽样字段哈希校验 → 幂等标记。

    - **冲突保护（审计二 R03）**：sqlite 侧已有同名项目、却没有 ``migrated:<pid>``
      标记（典型来源：Web/MCP 已经跑了 sqlite 后端并原生写入了该项目）→ 默认
      抛 ``RuntimeError`` 中止，绝不清空别人的存量；``force=True`` 时先做滚动
      备份（``backup(keep=3)``）再整项目 CASCADE 清空重导；
    - 幂等主语义：每项目迁移完成写项目级标记，再迁命中即整项目跳过；``INSERT OR
      REPLACE`` 仅作中断重跑的兜底；标记在校验通过后写入，失败重迁自愈；
    - 迁移先经 ``json_repo.create`` 幂等打开：触发 json 侧消息史规范化（窗口
      独有消息补写进 jsonl），否则旧版项目最近对话静默丢失；
    - messages 自增主键无稳定 id：导入前先清空该项目存量再整段重导（仅在已经
      确定要导入的项目上调用，不再是无条件清掉同名存量）；
    - 记录类一律走 raw-import（``_import_*``）保留原 id/时间戳，公开写方法会以
      ``_now()`` 重盖时间戳或生成新 id，破坏与 JSON 侧的全等；
    - JSON 目录原地保留（天然回滚：不设 ``FA_STORAGE`` 即回 JSON 只读使用）。
    """
    pids = [project_id] if project_id else [p["project_id"] for p in json_repo.list_projects()]
    report = {"projects": 0, "messages": 0, "field_sites": 0, "informants": 0,
              "search_cache": 0, "knowledge": 0, "proposals": 0, "confirmations": 0,
              "skipped": 0, "conflicts": 0, "backups": 0}
    conflicts = [pid for pid in pids
                 if not _already_migrated(sqlite_repo, pid) and _project_exists(sqlite_repo, pid)]
    report["conflicts"] = len(conflicts)
    if conflicts and not force:
        raise RuntimeError(
            "SQLite 侧已存在同名项目且无迁移标记（可能是后端切换后原生写入的存量）："
            f"{', '.join(conflicts)}。确认要覆盖请加 force=True"
            "（CLI: fieldwork migrate --to-sqlite --force，会先做滚动备份）")
    if conflicts and backup(sqlite_repo) is not None:
        report["backups"] = 1
    migrated: list[str] = []   # 本轮实际导入的 pid；_verify 通过后才落标记
    for pid in pids:
        if _already_migrated(sqlite_repo, pid):
            report["skipped"] += 1
            continue
        if pid in conflicts:            # 只有 force 路径能到这里：先清旧行
            _clear_project(sqlite_repo, pid)
        jstate = json_repo.get_state(pid)
        json_repo.create(pid)           # 幂等打开 = 触发 json 侧消息史规范化
        sqlite_repo.create(pid)                              # 幂等建项目
        sqlite_repo.set_phase(pid, jstate["phase"])          # 阶段（同阶段不写史）
        sqlite_repo._import_phase_history(pid, jstate["phase_history"])
        if jstate["research_context"]:
            sqlite_repo.update_research_context(pid, jstate["research_context"])
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
        for c in jstate.get("confirmations") or []:          # 确认记录（审计二 R09）
            sqlite_repo._import_confirmation(pid, c)
            report["confirmations"] += 1
        sqlite_repo._set_proposal_confirmed(pid, bool(jstate.get("proposal_confirmed")))
        migrated.append(pid)
        report["projects"] += 1
    _verify(json_repo, sqlite_repo, pids)   # 行数 + 抽样哈希；不一致抛 RuntimeError
    for pid in migrated:                    # 校验通过才标记（失败重迁自愈）
        _mark_project(sqlite_repo, pid)
    _mark_migrated(sqlite_repo)
    return report
