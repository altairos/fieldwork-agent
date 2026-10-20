"""``JsonRepository`` — ``ProjectRepository`` 的 JSON 文件实现（承接旧 ``core/state.py``）。

目录布局沿用旧版，存量项目目录可直接打开::

    {root}/{pid}/
        state.json                    # 项目状态（含消息滚动窗口 ≤50 条）
        chat_history.jsonl            # 全量消息史（唯一真源，读时只读这里）
        knowledge/{id}.json           # 知识库条目
        search_cache/{id}.json        # 搜索结果缓存（≤64KB，超限写截断标记）
        output/{kind}_{version}_{ts}.md   # 提案/纪要文档

实现要点：
- **无缓存**：每次调用现读现写 ``state.json``，实例可随时重建，跨实例读到的即磁盘真相；
- **单轨消息史**：``append_message`` 一次写 jsonl + 窗口，``get_messages`` 只读 jsonl，
  旧 ``get_recent_messages`` 的"窗口 + jsonl 双轨合并"已废弃；
- **存量规范化**：``create`` 对已存在项目幂等，并把旧版留在窗口里、未进 jsonl 的尾部
  消息补写进 jsonl（按 ts+role+content 去重，重复打开不会二次写入）；
- **读时补齐/归一**：新增状态键自动补默认值；旧缓存文件的 ``source_tool`` 读作 ``tool``；
  旧 ``proposal_draft`` / ``final_proposal`` 单值键并入 ``list_proposals``；
- **不写旧键**：新文档只进 ``proposals[]``，不再回写 ``proposal_draft``（避免双轨真源）；
- **per-pid 进程内锁（R11）**：同一项目的所有「读-改-写」变更方法在 ``_lock_for(pid)``
  内串行完成 load→改→save，防并发整份写回把他人已保存的字段（如 owner）回退为旧
  快照值；纯读方法与 ``create``/``delete_project`` 不包裹（非既有 state 的读改写）。
"""

import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fieldwork_agent.kernel.guidance.phases import PHASES
from fieldwork_agent.kernel.models import ConfirmationRecord
from fieldwork_agent.kernel.repository.cache_summary import result_summary
from fieldwork_agent.kernel.repository.confirm_hash import (
    ProposalConfirmError,
    compute_confirm_hash,
    compute_doc_hash,
)
from fieldwork_agent.kernel.repository.guard import (
    cache_id_violation,
    owner_violation,
    path_id_violation,
)
from fieldwork_agent.kernel.repository.protocol import _UNSET

# 消息滚动窗口上限（state 内只留最近 N 条，全量在 jsonl）
MESSAGE_WINDOW = 50
# 搜索缓存 result 落盘上限（UTF-8 字节）
CACHE_LIMIT_BYTES = 64 * 1024


def _now() -> str:
    return datetime.now().isoformat()


def _new_id(prefix: str, directory: Path | None = None) -> str:
    """时间戳前缀 + 同秒序号保证可排序且单调，随机尾仅防同秒碰撞（读取已按
    项目域收窄，审计二 R01）。

    文件名即 id 的调用方（knowledge / search_cache）传 directory：序号取该
    目录同秒已有文件数，文件名字典序 == 保存顺序——时钟分辨率不足
    （Windows ~15.6ms）时同秒条目不退化为随机 uid 字典序（否则确认指纹
    第四/五路随之随机抖动）。存 state 列表的调用方（site / inf）顺序由
    append 保证，传 None。
    """
    stamp = f"{datetime.now():%Y%m%d%H%M%S}"
    if directory is None:
        return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"
    seq = sum(1 for _ in directory.glob(f"{prefix}_{stamp}_*.json"))
    return f"{prefix}_{stamp}_{seq:04x}{uuid.uuid4().hex[:4]}"


def _default_state(project_id: str) -> dict:
    """新项目状态：旧 ``_default_state`` 全部键保留，另加 kernel 化的新键。"""
    return {
        "project_id": project_id,
        "created_at": _now(),
        "updated_at": _now(),
        "phase": PHASES[0],
        "phase_history": [],
        "owner": None,   # 归属（anon:<token> 或未来账号命名空间；None=无主）
        # 新：研究上下文（旧 researcher_info 保留只读，由宿主决定是否搬迁）
        "research_context": {},
        "dialogue_context": [],
        "field_sites": [],
        "informants": [],
        "proposals": [],
        "confirmations": [],
        "proposal_confirmed": False,
        "travel_plan": None,
        # 旧单值键（读兼容用，新写入一律进 proposals[]）
        "researcher_info": {},
        "proposal_draft": None,
        "final_proposal": None,
        "search_results": [],
        "notes": [],
    }


class JsonRepository:
    """以 JSON 文件树为后端的持久化实现（里程碑 1 默认启用，见 spec §5.1）。"""

    def __init__(self, projects_dir: Path | str):
        self._projects_dir = Path(projects_dir)
        # R11：per-pid 进程内锁表（每项目一把，随实例生存；项目数有界，不清理）
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, pid: str) -> threading.Lock:
        """取（并惰性建）该项目的进程内互斥锁：同 pid 恒返回同一把。

        所有「读-改-写整份 state」的变更方法必须持锁完成 load→改→save，
        保证同一 pid 的写串行（R11）。锁不可重入：被包裹的方法之间不得
        相互调用（现有代码只调私有工具与纯读公开方法，安全）。
        """
        with self._locks_guard:
            lock = self._locks.get(pid)
            if lock is None:
                lock = self._locks[pid] = threading.Lock()
            return lock

    # ------------------------------------------------------------------ 内部工具

    @property
    def root(self) -> Path:
        """项目根目录（非 Protocol 成员，仅供宿主排障与契约套件的截断用例直读）。"""
        return self._projects_dir

    def _project_dir(self, pid: str) -> Path:
        return self._projects_dir / pid

    def _state_file(self, pid: str) -> Path:
        return self._project_dir(pid) / "state.json"

    def _chat_file(self, pid: str) -> Path:
        return self._project_dir(pid) / "chat_history.jsonl"

    def _knowledge_dir(self, pid: str) -> Path:
        return self._project_dir(pid) / "knowledge"

    def _cache_dir(self, pid: str) -> Path:
        return self._project_dir(pid) / "search_cache"

    def _output_dir(self, pid: str) -> Path:
        return self._project_dir(pid) / "output"

    def _require(self, pid: str) -> None:
        """未知项目 → KeyError（契约套件钉死，双实现一致）。"""
        if not self._state_file(pid).exists():
            raise KeyError(f"未知项目: {pid}")

    def _load(self, pid: str) -> dict:
        """读状态并补齐新增键（存量项目无需离线迁移即可打开）。"""
        self._require(pid)
        state = self._read_json(self._state_file(pid)) or {}
        for key, value in _default_state(pid).items():
            state.setdefault(key, value)
        return state

    def _save(self, pid: str, state: dict) -> None:
        state["updated_at"] = _now()
        self._write_json(self._state_file(pid), state)

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        """读取 JSON。文件不存在/不可访问 → None；存在但解析失败 → ValueError。"""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"state.json 损坏: {path}: {exc}") from exc

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        """原子落盘：先写 tmp 再 os.replace（Windows 同卷可覆盖既有文件）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / (path.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, path)

    def _read_chat_log(self, pid: str) -> list[dict]:
        """读全量消息史；坏行跳过（旧文件可能因进程中断留下半行）。"""
        path = self._chat_file(pid)
        if not path.exists():
            return []
        messages = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return messages

    def _write_chat_lines(self, pid: str, entries: list[dict]) -> None:
        path = self._chat_file(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _rewrite_chat_lines(self, pid: str, entries: list[dict]) -> None:
        """整表重写消息史（弹末条用）：先写 tmp 再 os.replace，避免半行。"""
        path = self._chat_file(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / (path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.replace(tmp, path)

    def _backfill_chat_log(self, pid: str, state: dict) -> None:
        """存量迁移：旧版只把**溢出**部分写进 jsonl，最近若干条留在 state 窗口里。

        单轨制要求 jsonl 是全量真源，故首次打开时把窗口内尚未落盘的条目补写进去
        （按 ``(ts, role, content)`` 去重，重复打开不会产生重复行）。
        """
        window = state.get("dialogue_context") or []
        if not window:
            return
        seen = {
            (m.get("ts"), m.get("role"), m.get("content")) for m in self._read_chat_log(pid)
        }
        missing = [
            m for m in window if (m.get("ts"), m.get("role"), m.get("content")) not in seen
        ]
        if missing:
            self._write_chat_lines(pid, missing)

    def _upsert_record(self, records: list[dict], record: dict, prefix: str) -> dict:
        """同 id 覆盖、无 id 追加；返回入库后的记录（调用方持有 id）。"""
        entry = dict(record)
        entry["id"] = entry.get("id") or _new_id(prefix)
        entry["saved_at"] = _now()
        for index, existing in enumerate(records):
            if existing.get("id") == entry["id"]:
                records[index] = entry
                break
        else:
            records.append(entry)
        return entry

    # ------------------------------------------------------------------ 项目与阶段

    def create(self, project_id: str, owner: str | None = None) -> None:
        """幂等建项目：已存在则原样保留存量（顺带补齐消息史），不覆盖、不报错。

        owner 只在新项目时写入；对已存在项目（含无主存量）不覆盖——认领走 set_owner。
        """
        reason = owner_violation(owner)
        if reason:
            raise ValueError(f"非法 owner（{reason}）: {owner}")
        for sub in ("knowledge", "search_cache", "output"):
            self._project_dir(project_id).joinpath(sub).mkdir(parents=True, exist_ok=True)
        if self._state_file(project_id).exists():
            self._backfill_chat_log(project_id, self._load(project_id))
            return
        state = _default_state(project_id)
        state["owner"] = owner
        self._save(project_id, state)

    def delete_project(self, pid: str) -> None:
        """删除即整域清除：整个项目目录（state/消息史/知识/缓存/文档）一并移除。"""
        self._require(pid)
        shutil.rmtree(self._project_dir(pid))

    def list_projects(self, owner=_UNSET) -> list[dict]:
        """项目摘要列表：owner 三态（缺省全量 / None 仅无主 / str 仅该归属）。"""
        if not self._projects_dir.exists():
            return []
        summaries = []
        for pdir in sorted(self._projects_dir.iterdir()):
            if not (pdir / "state.json").exists():
                continue
            state = self._load(pdir.name)
            if owner is not _UNSET and state.get("owner") != owner:
                continue
            summaries.append(
                {
                    "project_id": state["project_id"],
                    "phase": state["phase"],
                    "created_at": state["created_at"],
                    "updated_at": state["updated_at"],
                }
            )
        return summaries

    def set_owner(self, pid: str, owner: str | None) -> None:
        """改写归属（账号认领预留）；幂等（同值直接返回）；未知项目 KeyError。"""
        reason = owner_violation(owner)
        if reason:
            raise ValueError(f"非法 owner（{reason}）: {owner}")
        with self._lock_for(pid):
            state = self._load(pid)
            if state.get("owner") == owner:
                return
            state["owner"] = owner
            self._save(pid, state)

    def set_owner_if(self, pid: str, expected: str | None, new: str | None) -> bool:
        for o in (expected, new):
            reason = owner_violation(o)
            if reason:
                raise ValueError(f"非法 owner（{reason}）: {o}")
        with self._lock_for(pid):
            state = self._load(pid)          # _load 对未知 pid 抛 KeyError
            if state.get("owner") != expected:
                return False
            if state.get("owner") == new:
                return True
            state["owner"] = new
            self._save(pid, state)
            return True

    def get_state(self, pid: str) -> dict:
        return self._load(pid)

    def get_phase(self, pid: str) -> str:
        return self._load(pid)["phase"]

    def set_phase(self, pid: str, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError(f"未知阶段: {phase}")
        with self._lock_for(pid):
            state = self._load(pid)
            current = state["phase"]
            if current == phase:
                return
            state["phase_history"].append({"from": current, "to": phase, "at": _now()})
            state["phase"] = phase
            self._save(pid, state)

    # ------------------------------------------------------------------ 研究上下文

    def update_research_context(self, pid: str, patch: dict) -> None:
        with self._lock_for(pid):
            state = self._load(pid)
            state["research_context"].update(patch)
            self._save(pid, state)

    def get_research_context(self, pid: str) -> dict:
        return dict(self._load(pid)["research_context"])

    # ------------------------------------------------------------------ 消息史

    def append_message(self, pid: str, role: str, content: str, meta: dict | None = None) -> None:
        with self._lock_for(pid):
            state = self._load(pid)
            self._backfill_chat_log(pid, state)
            entry = {"role": role, "content": content, "ts": _now(), "metadata": dict(meta or {})}
            self._write_chat_lines(pid, [entry])
            window = state["dialogue_context"]
            window.append(entry)
            del window[:-MESSAGE_WINDOW]  # 超出窗口的旧条目只存在于 jsonl
            self._save(pid, state)

    def get_messages(self, pid: str, limit: int = 30) -> list[dict]:
        self._require(pid)
        messages = self._read_chat_log(pid)
        if limit <= 0:
            return messages
        return messages[-limit:]

    def pop_last_message(self, pid: str, role: str | None = None) -> dict | None:
        """弹出最近一条（可按 role 过滤）：jsonl 整表重写 + state 窗口同步弹出。"""
        with self._lock_for(pid):
            state = self._load(pid)
            self._backfill_chat_log(pid, state)
            entries = self._read_chat_log(pid)
            index = next((i for i in range(len(entries) - 1, -1, -1)
                          if role is None or entries[i].get("role") == role), None)
            if index is None:
                return None
            popped = entries.pop(index)
            self._rewrite_chat_lines(pid, entries)
            window = state["dialogue_context"]
            key = (popped.get("ts"), popped.get("role"), popped.get("content"))
            for i in range(len(window) - 1, -1, -1):
                w = window[i]
                if (w.get("ts"), w.get("role"), w.get("content")) == key:
                    del window[i]
                    break
            self._save(pid, state)
            return popped

    # ------------------------------------------------------------------ 田野点与报道人

    def save_field_site(self, pid: str, record: dict) -> str:
        with self._lock_for(pid):
            state = self._load(pid)
            entry = self._upsert_record(state["field_sites"], record, "site")
            self._save(pid, state)
            return entry["id"]

    def list_field_sites(self, pid: str) -> list[dict]:
        return [dict(s) for s in self._load(pid)["field_sites"]]

    def save_informant(self, pid: str, record: dict) -> str:
        with self._lock_for(pid):
            state = self._load(pid)
            entry = self._upsert_record(state["informants"], record, "inf")
            self._save(pid, state)
            return entry["id"]

    def list_informants(self, pid: str) -> list[dict]:
        return [dict(i) for i in self._load(pid)["informants"]]

    # ------------------------------------------------------------------ 搜索缓存

    def cache_search(self, pid: str, query: str, tool: str, result: dict, *,
                     parent_call_id: str = "", turn_id: str = "",
                     query_key: str = "") -> str:
        with self._lock_for(pid):
            self._require(pid)
            cid = _new_id("srch", self._cache_dir(pid))
            cached_at = _now()
            serialized = json.dumps(result, ensure_ascii=False)
            payload = {"id": cid, "query": query, "tool": tool, "cached_at": cached_at,
                       "parent_call_id": parent_call_id, "turn_id": turn_id,
                       "query_key": query_key}
            if len(serialized.encode("utf-8")) > CACHE_LIMIT_BYTES:
                # 超限：整体替换为截断标记，preview 按 UTF-8 字节截断（不产生半个字符）
                preview = serialized.encode("utf-8")[:CACHE_LIMIT_BYTES].decode(
                    "utf-8", errors="ignore"
                )
                payload["result"] = {"truncated": True, "preview": preview}
                payload["truncated"], payload["empty"], payload["total"] = True, False, None
            else:
                payload["result"] = result
                empty, total = result_summary(result)
                payload["truncated"], payload["empty"], payload["total"] = False, empty, total
            self._write_json(self._cache_dir(pid) / f"{cid}.json", payload)
            return cid

    def list_cached_searches(self, pid: str) -> list[dict]:
        """缓存索引以文件为唯一真源（旧 state 里的 ``search_results`` 索引不再参与）。"""
        self._require(pid)
        index = []
        for path in sorted(self._cache_dir(pid).glob("*.json")):
            try:
                data = self._read_json(path)
            except ValueError:
                continue
            if data is None:
                continue
            entry = {
                "id": data.get("id", path.stem),
                "query": data.get("query", ""),
                # 旧文件键是 source_tool，读时归一为 tool
                "tool": data.get("tool") or data.get("source_tool", ""),
                "cached_at": data.get("cached_at", ""),
                "parent_call_id": data.get("parent_call_id") or "",
                "turn_id": data.get("turn_id") or "",
                "query_key": data.get("query_key") or "",
                "empty": data.get("empty"),
                "truncated": data.get("truncated"),
                "total": data.get("total"),
            }
            if entry["empty"] is None or entry["truncated"] is None:
                # 存量旧记录（R12）：Json 索引本就整读文件，现场派生零额外 IO
                res = data.get("result")
                if isinstance(res, dict) and res.get("truncated") is True:
                    entry["truncated"], entry["empty"], entry["total"] = True, False, None
                else:
                    empty, total = result_summary(res)
                    entry["truncated"], entry["empty"], entry["total"] = False, empty, total
            index.append(entry)
        index.sort(key=lambda e: (e["cached_at"], e["id"]))   # R15：与 SQLite 同序
        return index

    def get_cached_search(self, pid: str, cid: str) -> dict | None:
        """按项目域读取快照（审计二 R01）：cid 过白名单后只在 pid 的 search_cache 内定位。"""
        if cache_id_violation(cid):
            return None
        try:
            self._require(pid)
        except KeyError:
            return None
        trusted = self._cache_dir(pid).resolve()
        # 受信根包含校验（spec §1.3）：缓存目录自身 symlink/junction 外指时，
        # 仅比较条目父目录相等不够——先确认 trusted 仍在 projects_root 之下
        if not trusted.is_relative_to(self._projects_dir.resolve()):
            return None
        candidate = (trusted / f"{cid}.json").resolve()
        if candidate.parent != trusted:
            return None
        try:
            return self._read_json(candidate)
        except ValueError:
            return None

    # ------------------------------------------------------------------ 知识库

    def add_knowledge(
        self,
        pid: str,
        title: str,
        content: str,
        source: str = "user",
        tags: list[str] | None = None,
        category: str = "general",
    ) -> str:
        with self._lock_for(pid):
            self._require(pid)
            entry = {
                "id": _new_id("know", self._knowledge_dir(pid)),
                "title": title,
                "content": content,
                "source": source,
                "tags": list(tags or []),
                "category": category,
                "created_at": _now(),
            }
            self._write_json(self._knowledge_dir(pid) / f"{entry['id']}.json", entry)
            return entry["id"]

    def list_knowledge(self, pid: str) -> list[dict]:
        self._require(pid)
        entries = []
        for path in sorted(self._knowledge_dir(pid).glob("*.json")):
            try:
                data = self._read_json(path)
            except ValueError:
                continue
            if data is not None:
                entries.append(data)
        return entries

    def search_knowledge(self, pid: str, text: str) -> list[dict]:
        """标题+正文+标签拼为检索串，空白分词后要求全部命中（FTS5 属里程碑 2）。"""
        tokens = [t for t in text.lower().split() if t]
        if not tokens:
            return self.list_knowledge(pid)
        matched = []
        for entry in self.list_knowledge(pid):
            haystack = " ".join(
                [entry.get("title", ""), entry.get("content", ""), " ".join(entry.get("tags", []))]
            ).lower()
            if all(token in haystack for token in tokens):
                matched.append(entry)
        return matched

    def delete_knowledge(self, pid: str, entry_id: str) -> None:
        # P2-2：entry_id 必须是单段文件名（拒绝 ../、\、绝对路径），
        # 防拼路径越 knowledge 目录删任意文件。校验走 guard 单一来源：
        # Path(entry_id).name 是平台语义，Linux 不把 \ 当分隔符，会放过
        # Windows 风格穿越串（..\..\victim 等，CI ubuntu 红）。
        violation = path_id_violation(entry_id, field="entry_id")
        if violation:
            raise ValueError(f"非法知识条目 id（{violation}）: {entry_id!r}")
        with self._lock_for(pid):
            path = self._knowledge_dir(pid) / f"{entry_id}.json"
            if path.exists():
                path.unlink()

    def update_knowledge(
        self,
        pid: str,
        entry_id: str,
        title: str | None = None,
        content: str | None = None,
    ) -> dict:
        # P2-2 同源守卫（见 delete_knowledge 注释）：entry_id 单段文件名，
        # 防拼路径越 knowledge 目录改写任意文件。
        violation = path_id_violation(entry_id, field="entry_id")
        if violation:
            raise ValueError(f"非法知识条目 id（{violation}）: {entry_id!r}")
        with self._lock_for(pid):
            self._require(pid)
            path = self._knowledge_dir(pid) / f"{entry_id}.json"
            entry = self._read_json(path)
            if entry is None:
                raise KeyError(entry_id)
            if title is not None:
                entry["title"] = title
            if content is not None:
                entry["content"] = content
            self._write_json(path, entry)
            return entry

    # ------------------------------------------------------------------ 提案文档

    def save_proposal(
        self, pid: str, markdown: str, kind: str = "proposal", version: str = "draft",
        *, provenance_ctx: dict | None = None,
        provenance: dict | None = None,
    ) -> str:
        """落盘一份提案文档（版本累积，绝不覆盖）。

        ``provenance``（评审 R02）：正文五渠道生成输入
        （ctx/sites/informants/knowledge/itinerary），落盘指纹按它算——
        即“正文到底用了什么”，而不是保存时现读的仓储推测值。
        ``provenance_ctx`` 为旧调用面的 ctx 单路别名。
        """
        with self._lock_for(pid):
            state = self._load(pid)
            uid = uuid.uuid4().hex[:8]
            record = {
                "id": f"prop_{datetime.now():%Y%m%d%H%M%S}_{uid}",
                "kind": kind,
                "version": version,
                "filename": f"{kind}_{version}_{datetime.now():%Y%m%d_%H%M%S}.md",
                "saved_at": _now(),
                # 审计二 R09：保存时落五路依赖指纹，确认时校验。
                # N04：provenance 非 None 时用正文实际生成输入算指纹（而非保存时
                # 按当前仓储推测），防止正文旧、指纹新的错配；R02：扩到五渠道。
                "dep_hash": self._doc_inputs_hash(pid, state, provenance, provenance_ctx),
            }
            path = self._output_dir(pid) / record["filename"]
            if path.exists():
                # 同秒重复保存：追加随机后缀，绝不覆盖既有文档（版本累积是契约要求）
                record["filename"] = record["filename"][:-3] + f"_{uid}.md"
                path = self._output_dir(pid) / record["filename"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
            state["proposals"].append(record)
            self._save(pid, state)
            return record["id"]

    def _proposal_doc(self, pid: str, record: dict) -> dict:
        doc = dict(record)
        # R09 前的记录无 dep_hash 键：读出统一补空串（空串=无指纹，与
        # confirm/stale 的 falsy 判定同义），保 sqlite 侧 list_proposals
        # 七键形态平价——迁移全等校验（core/migrate._verify）依赖此形状。
        doc.setdefault("dep_hash", "")
        path = self._output_dir(pid) / record.get("filename", "")
        doc["content"] = path.read_text(encoding="utf-8") if path.exists() else ""
        return doc

    def list_proposals(self, pid: str) -> list[dict]:
        state = self._load(pid)
        docs = [self._proposal_doc(pid, r) for r in state["proposals"]]
        # 存量：旧版只有 proposal_draft / final_proposal 两个单值键
        for key, version in (("proposal_draft", "draft"), ("final_proposal", "final")):
            legacy = state.get(key)
            if isinstance(legacy, dict) and legacy.get("filename"):
                docs.append(
                    self._proposal_doc(
                        pid,
                        {
                            "id": f"legacy_{key}",
                            "kind": "proposal",
                            "version": version,
                            "filename": legacy["filename"],
                            "saved_at": legacy.get("saved_at", ""),
                            "dep_hash": "",
                        },
                    )
                )
        # 平局只用 saved_at：时钟分辨率不够时（Windows ~15.6ms）同 tick 保存的
        # saved_at 相同，稳定排序保持保存顺序（append 顺序即版本顺序）；
        # id 尾部是随机 uid 无单调性，绝不能做 tie-break（CI windows 矩阵间歇红）。
        docs.sort(key=lambda d: d.get("saved_at") or "")
        return docs

    def get_latest_proposal(self, pid: str) -> dict | None:
        docs = self.list_proposals(pid)
        return docs[-1] if docs else None

    # ---------------------------------------------------------------- 确认与失效（第二批 F04）

    def confirm_field_site(self, pid: str, site_id: str) -> bool:
        """按记录 id 把田野点置为已确认；未知 id 返回 False（幂等）。"""
        with self._lock_for(pid):
            state = self._load(pid)
            for site in state["field_sites"]:
                if site.get("id") == site_id:
                    site["confirmed"] = True
                    state["updated_at"] = _now()
                    self._save(pid, state)
                    return True
            return False

    def _current_confirm_hash(self, pid: str, state: dict, *,
                              provenance_ctx: dict | None = None) -> str:
        """旧代五路依赖指纹（ctx/田野点/报道人/知识库/最新行程快照 id）。

        仅用于识别存量文档的旧指纹；写出侧走 ``_doc_inputs_hash``。
        """
        return compute_confirm_hash(
            provenance_ctx if provenance_ctx is not None
            else (state.get("research_context") or {}),
            self.list_field_sites(pid),
            self.list_informants(pid),
            self.list_knowledge(pid),
            self._latest_itinerary_cid(pid))

    def _doc_inputs_hash(self, pid: str, state: dict,
                         provenance: dict | None,
                         provenance_ctx: dict | None = None) -> str:
        """落盘指纹：有 provenance 按正文生成输入算（新代），否则按仓储现读算（旧代）。

        provenance 中缺省的渠道回落到仓储现值（旧调用面只需传 ctx）。
        """
        if provenance is None:
            return self._current_confirm_hash(pid, state, provenance_ctx=provenance_ctx)
        ctx = provenance.get("ctx", provenance_ctx
                             if provenance_ctx is not None
                             else (state.get("research_context") or {}))
        _, itinerary = self._latest_itinerary(pid)
        return compute_doc_hash(
            ctx,
            provenance.get("sites", self.list_field_sites(pid)),
            provenance.get("informants", self.list_informants(pid)),
            provenance.get("knowledge", self.list_knowledge(pid)),
            provenance.get("itinerary", itinerary))

    def _confirm_candidates(self, pid: str, state: dict) -> tuple[str, str, str]:
        """当前仓储的三种指纹：v3（事实输入）、v2（旧代含软字段）、
        旧代（行程只装 cid）。

        确认/失效判定只要命中任一即视为未变——存量文档保留可确认性，
        新写文档则按事实输入判定。
        """
        _, itinerary = self._latest_itinerary(pid)
        ctx = state.get("research_context") or {}
        sites = self.list_field_sites(pid)
        informants = self.list_informants(pid)
        knowledge = self.list_knowledge(pid)
        return (
            compute_doc_hash(ctx, sites, informants, knowledge, itinerary),
            compute_doc_hash(ctx, sites, informants, knowledge, itinerary,
                             include_soft_ctx=True),
            compute_confirm_hash(ctx, sites, informants, knowledge,
                                 self._latest_itinerary_cid(pid)),
        )

    def _latest_itinerary(self, pid: str) -> tuple[str, dict | None]:
        """最新一次 compose_itinerary 快照的 (id, result)（无则 ('', None)）。"""
        cid = self._latest_itinerary_cid(pid)
        if not cid:
            return "", None
        record = self.get_cached_search(pid, cid) or {}
        return cid, record.get("result")

    def _latest_itinerary_cid(self, pid: str) -> str:
        """最新一次 compose_itinerary 快照 id（无则空串）——五路指纹之⑤。

        list_cached_searches 按文件名（时间戳前缀）升序，最新在末尾。
        """
        for cached in reversed(self.list_cached_searches(pid)):
            if cached.get("tool") == "compose_itinerary":
                return cached["id"]
        return ""

    def confirm_proposal(self, pid: str, doc_id: str = "") -> dict:
        """审计二 R09：确认**指定**文档（doc_id 空则取最新一份，兼容旧调用面）。

        四重校验均 raise ``ProposalConfirmError``：文档不存在 ``not_found``、
        类型非 proposal ``wrong_kind``、缺依赖指纹 ``dep_hash_missing``（legacy
        文档）、指纹不一致 ``dep_hash_mismatch``（五路依赖已变化）。
        """
        with self._lock_for(pid):
            state = self._load(pid)
            if not doc_id:
                # P2-1：取“最新一份 proposal”，筛掉 interview_guide 等其他 kind（对齐 sqlite）
                proposals = [r for r in state.get("proposals") or []
                             if r.get("kind", "proposal") == "proposal"]
                doc_id = proposals[-1]["id"] if proposals else ""
            record = next(
                (r for r in state.get("proposals") or [] if r.get("id") == doc_id), None)
            if record is None:
                raise ProposalConfirmError(
                    f"文档不存在: {doc_id or '（尚无文档）'}", "not_found")
            if record.get("kind", "proposal") != "proposal":
                raise ProposalConfirmError(
                    f"文档类型为 {record.get('kind')}，只能确认 proposal 文档", "wrong_kind")
            current = self._confirm_candidates(pid, state)
            if not record.get("dep_hash"):
                raise ProposalConfirmError(
                    "该文档缺少依赖指纹（旧版生成）：请重新生成后再确认", "dep_hash_missing")
            if record["dep_hash"] not in current:
                raise ProposalConfirmError(
                    "研究输入已变化，该文档的确认已过期：请重新生成方案", "dep_hash_mismatch")
            rec = ConfirmationRecord(
                kind="proposal", subject=doc_id, confirmed_at=_now(), dep_hash=current[0])
            state.setdefault("confirmations", []).append(rec.model_dump(mode="json"))
            state["proposal_confirmed"] = True
            state["updated_at"] = rec.confirmed_at
            self._save(pid, state)
            return rec.model_dump(mode="json")

    def get_proposal_by_id(self, pid: str, doc_id: str) -> dict | None:
        """按记录 id 取文档（含 content 正文，兼容 legacy_ 前缀）；不存在返回 None。"""
        for record in self.list_proposals(pid):
            if record.get("id") == doc_id:
                return record
        return None

    def is_proposal_confirmation_stale(self, pid: str) -> bool:
        """五路依赖指纹与确认时不一致，或确认后产生了新 proposal 文档 → True。

        无确认记录 → False（从未确认，无所谓失效）；legacy 记录无
        ``dep_hash`` → True（无法判定依赖是否变化，保守要求重新确认）；
        新增 ``interview_guide`` 不使 proposal 确认失效（spec §3 按类型判定）。
        """
        state = self._load(pid)
        records = state.get("confirmations") or []
        if not records:
            return False
        latest = next(
            (r["id"] for r in reversed(state.get("proposals") or [])
             if r.get("kind", "proposal") == "proposal"), "")
        subject = records[-1].get("subject", "")
        if subject and latest and subject != latest:
            return True  # 确认后产生了新 proposal（产物侧失效，spec §3 按类型判定）
        dep_hash = records[-1].get("dep_hash", "")
        if not dep_hash:
            return True  # legacy 确认记录无指纹，保守视失效
        return dep_hash not in self._confirm_candidates(pid, state)
