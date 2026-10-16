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
- **不写旧键**：新文档只进 ``proposals[]``，不再回写 ``proposal_draft``（避免双轨真源）。
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from fieldwork_agent.kernel.guidance.phases import PHASES
from fieldwork_agent.kernel.models import ConfirmationRecord, compute_inputs_hash, inputs_stale
from fieldwork_agent.kernel.repository.guard import cache_id_violation

# 消息滚动窗口上限（state 内只留最近 N 条，全量在 jsonl）
MESSAGE_WINDOW = 50
# 搜索缓存 result 落盘上限（UTF-8 字节）
CACHE_LIMIT_BYTES = 64 * 1024
# 方案确认的关键输入字段（审计 F04：这些字段变更后旧确认失效）
CONFIRM_INPUT_FIELDS = ("travel_date", "budget", "time_window",
                        "departure_city", "target_location", "scale")


def _now() -> str:
    return datetime.now().isoformat()


def _new_id(prefix: str) -> str:
    """时间戳前缀保证可排序，随机后缀防同秒碰撞（读取已按项目域收窄，审计二 R01）。"""
    return f"{prefix}_{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"


def _default_state(project_id: str) -> dict:
    """新项目状态：旧 ``_default_state`` 全部键保留，另加 kernel 化的新键。"""
    return {
        "project_id": project_id,
        "created_at": _now(),
        "updated_at": _now(),
        "phase": PHASES[0],
        "phase_history": [],
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

    def create(self, project_id: str) -> None:
        """幂等建项目：已存在则原样保留存量（顺带补齐消息史），不覆盖、不报错。"""
        for sub in ("knowledge", "search_cache", "output"):
            self._project_dir(project_id).joinpath(sub).mkdir(parents=True, exist_ok=True)
        if self._state_file(project_id).exists():
            self._backfill_chat_log(project_id, self._load(project_id))
            return
        self._save(project_id, _default_state(project_id))

    def list_projects(self) -> list[dict]:
        if not self._projects_dir.exists():
            return []
        summaries = []
        for pdir in sorted(self._projects_dir.iterdir()):
            if not (pdir / "state.json").exists():
                continue
            state = self._load(pdir.name)
            summaries.append(
                {
                    "project_id": state["project_id"],
                    "phase": state["phase"],
                    "created_at": state["created_at"],
                    "updated_at": state["updated_at"],
                }
            )
        return summaries

    def get_state(self, pid: str) -> dict:
        return self._load(pid)

    def get_phase(self, pid: str) -> str:
        return self._load(pid)["phase"]

    def set_phase(self, pid: str, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError(f"未知阶段: {phase}")
        state = self._load(pid)
        current = state["phase"]
        if current == phase:
            return
        state["phase_history"].append({"from": current, "to": phase, "at": _now()})
        state["phase"] = phase
        self._save(pid, state)

    # ------------------------------------------------------------------ 研究上下文

    def update_research_context(self, pid: str, patch: dict) -> None:
        state = self._load(pid)
        state["research_context"].update(patch)
        self._save(pid, state)

    def get_research_context(self, pid: str) -> dict:
        return dict(self._load(pid)["research_context"])

    # ------------------------------------------------------------------ 消息史

    def append_message(self, pid: str, role: str, content: str, meta: dict | None = None) -> None:
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

    # ------------------------------------------------------------------ 田野点与报道人

    def save_field_site(self, pid: str, record: dict) -> str:
        state = self._load(pid)
        entry = self._upsert_record(state["field_sites"], record, "site")
        self._save(pid, state)
        return entry["id"]

    def list_field_sites(self, pid: str) -> list[dict]:
        return [dict(s) for s in self._load(pid)["field_sites"]]

    def save_informant(self, pid: str, record: dict) -> str:
        state = self._load(pid)
        entry = self._upsert_record(state["informants"], record, "inf")
        self._save(pid, state)
        return entry["id"]

    def list_informants(self, pid: str) -> list[dict]:
        return [dict(i) for i in self._load(pid)["informants"]]

    # ------------------------------------------------------------------ 搜索缓存

    def cache_search(self, pid: str, query: str, tool: str, result: dict) -> str:
        self._require(pid)
        cid = _new_id("srch")
        cached_at = _now()
        serialized = json.dumps(result, ensure_ascii=False)
        payload = {"id": cid, "query": query, "tool": tool, "cached_at": cached_at}
        if len(serialized.encode("utf-8")) > CACHE_LIMIT_BYTES:
            # 超限：整体替换为截断标记，preview 按 UTF-8 字节截断（不产生半个字符）
            preview = serialized.encode("utf-8")[:CACHE_LIMIT_BYTES].decode(
                "utf-8", errors="ignore"
            )
            payload["result"] = {"truncated": True, "preview": preview}
        else:
            payload["result"] = result
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
            index.append(
                {
                    "id": data.get("id", path.stem),
                    "query": data.get("query", ""),
                    # 旧文件键是 source_tool，读时归一为 tool
                    "tool": data.get("tool") or data.get("source_tool", ""),
                    "cached_at": data.get("cached_at", ""),
                }
            )
        return index

    def get_cached_search(self, pid: str, cid: str) -> dict | None:
        """按项目域读取快照（审计二 R01）：cid 过白名单后只在 pid 的 search_cache 内定位。"""
        if cache_id_violation(cid):
            return None
        try:
            self._require(pid)
        except KeyError:
            return None
        trusted = (self._cache_dir(pid)).resolve()
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
        self._require(pid)
        entry = {
            "id": _new_id("know"),
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
        path = self._knowledge_dir(pid) / f"{entry_id}.json"
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------ 提案文档

    def save_proposal(
        self, pid: str, markdown: str, kind: str = "proposal", version: str = "draft"
    ) -> str:
        state = self._load(pid)
        uid = uuid.uuid4().hex[:8]
        record = {
            "id": f"prop_{datetime.now():%Y%m%d%H%M%S}_{uid}",
            "kind": kind,
            "version": version,
            "filename": f"{kind}_{version}_{datetime.now():%Y%m%d_%H%M%S}.md",
            "saved_at": _now(),
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
                        },
                    )
                )
        docs.sort(key=lambda d: (d.get("saved_at") or "", d.get("id") or ""))
        return docs

    def get_latest_proposal(self, pid: str) -> dict | None:
        docs = self.list_proposals(pid)
        return docs[-1] if docs else None

    # ---------------------------------------------------------------- 确认与失效（第二批 F04）

    def confirm_field_site(self, pid: str, site_id: str) -> bool:
        """按记录 id 把田野点置为已确认；未知 id 返回 False（幂等）。"""
        state = self._load(pid)
        for site in state["field_sites"]:
            if site.get("id") == site_id:
                site["confirmed"] = True
                state["updated_at"] = _now()
                self._save(pid, state)
                return True
        return False

    def confirm_proposal(self, pid: str, subject: str = "") -> dict:
        """记录一次方案确认：落 ConfirmationRecord（含关键输入哈希）并置 proposal_confirmed。"""
        state = self._load(pid)
        ctx = state.get("research_context") or {}
        payload = {f: str(ctx.get(f, "") or "") for f in CONFIRM_INPUT_FIELDS}
        latest_id = state["proposals"][-1]["id"] if state.get("proposals") else ""
        record = ConfirmationRecord(
            kind="proposal", subject=subject or latest_id,
            confirmed_at=_now(), inputs_hash=compute_inputs_hash(payload))
        state.setdefault("confirmations", []).append(record.model_dump(mode="json"))
        state["proposal_confirmed"] = True
        state["updated_at"] = record.confirmed_at
        self._save(pid, state)
        return record.model_dump(mode="json")

    def get_proposal_by_id(self, pid: str, doc_id: str) -> dict | None:
        """按记录 id 取文档（含 content 正文，兼容 legacy_ 前缀）；不存在返回 None。"""
        for record in self.list_proposals(pid):
            if record.get("id") == doc_id:
                return record
        return None

    def is_proposal_confirmation_stale(self, pid: str) -> bool:
        """输入哈希与当前不一致，或确认后产生了新文档 → True；无确认记录返回 False。"""
        state = self._load(pid)
        records = state.get("confirmations") or []
        if not records:
            return False
        latest = state["proposals"][-1]["id"] if state.get("proposals") else ""
        subject = records[-1].get("subject", "")
        if subject and latest and subject != latest:
            return True  # 确认后产生了新文档（产物侧失效，终审 M1）
        ctx = state.get("research_context") or {}
        payload = {f: str(ctx.get(f, "") or "") for f in CONFIRM_INPUT_FIELDS}
        return inputs_stale(records[-1].get("inputs_hash", ""), compute_inputs_hash(payload))
