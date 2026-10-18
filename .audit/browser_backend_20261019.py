"""Real FastAPI + built React audit, with local scripted provider substitutes."""
import json
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from conftest import FakeConfig, FakeFlyAI

import uvicorn
from fieldwork_agent.kernel import assets
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository
from fieldwork_agent.web_host.app import create_app


class Model:
    def __init__(self, repo):
        self.repo = repo

    def chat(self, *args, **kwargs):
        return {"content": ""}  # Real document capability's template fallback

    def chat_stream(self, messages, **kwargs):
        text = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if messages[-1]["role"] == "tool":
            reply = text + " 完成。"
            yield {"delta": reply}
            yield {"content": reply, "tool_calls": None, "finish_reason": "stop", "usage": {}}
            return
        calls = []
        if "AUDIT_SITE" in text:
            time.sleep(1.0)  # Ensure initial browser snapshot loads before provider result
            calls = [
                {"id": "state", "name": "update_research_context", "arguments": {"patch": {
                    "title": "审计临时项目", "discipline": "社会学", "topic": "乡村组织",
                    "research_questions": "乡村组织如何参与地方治理？", "site_status": "待入场",
                    "scale": "单人", "budget": "10000", "time_window": "2026年11月",
                    "departure_city": "昆明", "target_location": "喜洲", "travel_date": "2026-11-01",
                    "purpose": "global", "interviews_not_applicable": True,
                    "interviews_na_reason": "本测试仅观察与档案研究"}}},
                {"id": "site", "name": "profile_field_site", "arguments": {"location": "喜洲"}},
            ]
        elif "AUDIT_ITINERARY" in text:
            calls = [{"id": "itinerary", "name": "compose_itinerary", "arguments": {
                "departure": "昆明", "destinations": ["喜洲"], "start_date": "2026-11-01",
                "duration_days": 3, "budget_total": 10000}}]
        elif "AUDIT_PROPOSAL" in text:
            pid = self.repo.list_projects()[0]["project_id"]
            it = next(c for c in reversed(self.repo.list_cached_searches(pid)) if c["tool"] == "compose_itinerary")
            calls = [{"id": "proposal", "name": "compose_proposal", "arguments": {
                "sites": self.repo.list_field_sites(pid), "ctx": {}, "informants": [], "knowledge": [],
                "itinerary": self.repo.get_cached_search(pid, it["id"])["result"]}}]
        yield {"content": "", "tool_calls": calls, "finish_reason": "tool_calls", "usage": {}}


def run(backend, root, port):
    cfg = FakeConfig(root)
    repo = JsonRepository(cfg.projects_dir) if backend == "json" else SqliteRepository(root / "db.sqlite")
    app = create_app(config=cfg, repo=repo, web_dist=ROOT / "web" / "dist")
    old = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with old(app):
            app.state.llm = Model(repo)
            app.state.flyai = FakeFlyAI()
            yield

    app.router.lifespan_context = lifespan
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        result = subprocess.run(["node", str(ROOT / ".audit" / "browser_20261019.cjs"), str(port), backend],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=100)
        print(result.stdout, flush=True)
        if result.returncode:
            print(result.stderr, flush=True)
        for summary in repo.list_projects():
            state = repo.get_state(summary["project_id"])
            print(json.dumps({"backend": backend, "final_phase": state["phase"],
                              "sites": len(state["field_sites"]), "docs": len(state["proposals"]),
                              "confirmed": state["proposal_confirmed"],
                              "stale": repo.is_proposal_confirmation_stale(summary["project_id"])}, ensure_ascii=False), flush=True)
            for handle in app.state.turn_manager._turns.values():
                for event in app.state.turn_manager.replay(handle.project_id, handle.turn_id, 0):
                    if event.event == "error":
                        print(json.dumps({"backend": backend, "turn_error": event.data}, ensure_ascii=False), flush=True)
    finally:
        server.should_exit = True
        thread.join(5)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="fieldwork-browser-audit-") as temp:
        root = Path(temp)
        empty = root / "assets"
        empty.mkdir()
        assets.reload(empty)
        for idx, backend in enumerate(["json", "sqlite"]):
            run(backend, root / backend, 8437 + idx)
