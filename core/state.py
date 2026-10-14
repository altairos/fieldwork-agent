"""
田野罗盘 Agent — 项目状态管理
每个田野调查项目独立存储，支持多轮对话、知识库、搜索结果缓存
"""
import json
from datetime import datetime
from typing import Any, Optional

from .config import Config


class ProjectState:
    """单个田野调查项目的完整状态"""

    # 四阶段访谈流程
    PHASE_RESEARCH_POSITIONING = "research_positioning"       # A: 研究定位
    PHASE_FIELD_SITE_PROFILES = "field_site_profiles"          # B: 田野点画像
    PHASE_ITINERARY_PLANNING = "itinerary_planning"            # C: 行程规划
    PHASE_PROPOSAL_GENERATION = "proposal_generation"          # D: 方案生成
    PHASE_FINISHED = "finished"                                # 完成

    PHASES_ORDER = [
        PHASE_RESEARCH_POSITIONING,
        PHASE_FIELD_SITE_PROFILES,
        PHASE_ITINERARY_PLANNING,
        PHASE_PROPOSAL_GENERATION,
        PHASE_FINISHED,
    ]

    def __init__(self, config: Config, project_id: str):
        self.config = config
        self.project_id = project_id
        self.project_dir = config.get_project_dir(project_id)
        self.state_file = self.project_dir / "state.json"
        self.chat_log_file = self.project_dir / "chat_history.jsonl"
        self.knowledge_dir = self.project_dir / "knowledge"
        self.output_dir = self.project_dir / "output"
        self.search_cache_dir = self.project_dir / "search_cache"

        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.search_cache_dir.mkdir(parents=True, exist_ok=True)

        self.state = self._load_state()

    def _load_state(self) -> dict:
        if self.state_file.exists():
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return self._default_state()

    def _default_state(self) -> dict:
        return {
            "project_id": self.project_id,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "phase": self.PHASE_RESEARCH_POSITIONING,
            "phase_history": [],
            "dialogue_context": [],
            "researcher_info": {},
            "field_sites": [],
            "travel_plan": None,
            "proposal_draft": None,
            "final_proposal": None,
            "search_results": [],
            "notes": [],
        }

    def save_state(self):
        self.state["updated_at"] = datetime.now().isoformat()
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    @property
    def phase(self) -> str:
        return self.state["phase"]

    @phase.setter
    def phase(self, value: str):
        if value not in self.PHASES_ORDER:
            raise ValueError(f"Unknown phase: {value}")
        if value != self.state["phase"]:
            self.state["phase_history"].append({
                "from": self.state["phase"],
                "to": value,
                "at": datetime.now().isoformat(),
            })
        self.state["phase"] = value
        self.save_state()

    def advance_phase(self):
        idx = self.PHASES_ORDER.index(self.phase)
        if idx < len(self.PHASES_ORDER) - 1:
            self.phase = self.PHASES_ORDER[idx + 1]

    def set_researcher_info(self, key: str, value: Any):
        self.state["researcher_info"][key] = value
        self.save_state()

    def get_researcher_info(self, key: str, default=None) -> Any:
        return self.state["researcher_info"].get(key, default)

    def get_all_researcher_info(self) -> dict:
        return self.state["researcher_info"]

    def append_chat(self, role: str, content: str, metadata: Optional[dict] = None):
        entry = {
            "role": role,
            "content": content,
            "ts": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self.state["dialogue_context"].append(entry)
        if len(self.state["dialogue_context"]) > 50:
            overflow = self.state["dialogue_context"][:-50]
            with open(self.chat_log_file, "a", encoding="utf-8") as f:
                for e in overflow:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            self.state["dialogue_context"] = self.state["dialogue_context"][-50:]
        self.save_state()

    def get_recent_messages(self, n: int = 30) -> list:
        msgs = list(self.state["dialogue_context"])
        if self.chat_log_file.exists():
            with open(self.chat_log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        msgs.append(json.loads(line))
        return msgs[-n:]

    # ─── 知识库 ──────────────────────────────

    def add_knowledge_entry(
        self, title: str, content: str, source: str = "user",
        tags: Optional[list] = None, category: str = "general"
    ):
        entry_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(self.get_knowledge_entries())}"
        entry = {
            "id": entry_id,
            "title": title,
            "content": content,
            "source": source,
            "tags": tags or [],
            "category": category,
            "created_at": datetime.now().isoformat(),
        }
        entry_file = self.knowledge_dir / f"{entry_id}.json"
        with open(entry_file, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2, ensure_ascii=False)
        self.save_state()
        return entry_id

    def get_knowledge_entries(self, category: Optional[str] = None, tags: Optional[list] = None) -> list:
        entries = []
        for fpath in sorted(self.knowledge_dir.glob("*.json")):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                if category and entry.get("category") != category:
                    continue
                if tags:
                    if not any(t in entry.get("tags", []) for t in tags):
                        continue
                entries.append(entry)
            except (json.JSONDecodeError, IOError):
                continue
        return entries

    def delete_knowledge_entry(self, entry_id: str):
        entry_file = self.knowledge_dir / f"{entry_id}.json"
        if entry_file.exists():
            entry_file.unlink()
            self.save_state()

    # ─── 搜索结果缓存 ──────────────────────

    def cache_search_result(self, query: str, result: dict, source_tool: str = "flyai"):
        cache_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(self.get_cached_searches())}"
        cached = {
            "id": cache_id,
            "query": query,
            "result": result,
            "source_tool": source_tool,
            "cached_at": datetime.now().isoformat(),
        }
        cache_file = self.search_cache_dir / f"{cache_id}.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cached, f, indent=2, ensure_ascii=False)
        self.state["search_results"].append({
            "id": cache_id,
            "query": query,
            "source_tool": source_tool,
            "cached_at": cached["cached_at"],
        })
        self.save_state()
        return cache_id

    def get_cached_searches(self) -> list:
        results = list(self.state.get("search_results", []))
        for fpath in sorted(self.search_cache_dir.glob("*.json")):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cid = data.get("id", "")
                if not any(r["id"] == cid for r in results):
                    results.append({
                        "id": cid,
                        "query": data.get("query", ""),
                        "source_tool": data.get("source_tool", ""),
                        "cached_at": data.get("cached_at", ""),
                    })
            except (json.JSONDecodeError, IOError):
                continue
        return results

    def get_search_result_by_id(self, cache_id: str) -> Optional[dict]:
        fpath = self.search_cache_dir / f"{cache_id}.json"
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    # ─── 提案文档 ──────────────────────────────

    def save_proposal(self, markdown_content: str, version: str = "draft"):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"proposal_{version}_{ts}.md"
        filepath = self.output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        if version == "draft":
            self.state["proposal_draft"] = {"filename": filename, "saved_at": datetime.now().isoformat()}
        else:
            self.state["final_proposal"] = {"filename": filename, "saved_at": datetime.now().isoformat()}
        self.save_state()
        return filepath

    def get_latest_proposal(self) -> Optional[str]:
        if self.state.get("final_proposal"):
            fp = self.output_dir / self.state["final_proposal"]["filename"]
        elif self.state.get("proposal_draft"):
            fp = self.output_dir / self.state["proposal_draft"]["filename"]
        else:
            return None
        if fp.exists():
            with open(fp, "r", encoding="utf-8") as f:
                return f.read()
        return None

    def add_note(self, content: str):
        note = {"content": content, "created_at": datetime.now().isoformat()}
        self.state["notes"].append(note)
        self.save_state()
