"""Isolated audit probes. Never uses user data, secrets, or provider networks."""
import asyncio
import copy
import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from conftest import FakeConfig
from test_web_agent_loop import FakeLLM, FakeFlyAI, _PROPOSAL_ARGS

import httpx
from fieldwork_agent.kernel import assets
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository
from fieldwork_agent.web_host.app import create_app
from fieldwork_agent.web_host.turn_manager import TurnManager
from fieldwork_agent.web_host.agent import loop
from fieldwork_agent.core.migrate import migrate_to_sqlite
from fieldwork_agent.kernel.aggregation.itinerary import compose_itinerary
from fieldwork_agent.kernel.models import ToolError


def emit(name, **fields):
    print(json.dumps({"probe": name, **fields}, ensure_ascii=False), flush=True)


class ReplyLLM:
    def __init__(self):
        self.calls = 0
    def chat_stream(self, messages, **kw):
        self.calls += 1
        return iter([{"delta": "完整回答。"}, {"content": "完整回答。", "tool_calls": None,
                     "finish_reason": "stop", "usage": {}}])
    def chat(self, *a, **kw):
        return {"content": ""}


async def http_probes(root, backend):
    cfg = FakeConfig(root)
    cfg.app_token = "audit-app-token"
    repo = (JsonRepository(cfg.projects_dir) if backend == "json"
            else SqliteRepository(root / "db.sqlite"))
    repo.create("victim", owner="anon:alice")
    repo.update_research_context("victim", {"title": "Stable title", "topic": "PRIVATE_SENTINEL"})
    app = create_app(config=cfg, repo=repo, web_dist=root / "no-dist")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://audit",
                                     headers={"X-App-Token": cfg.app_token}) as c:
            denied = await c.get("/projects/victim", headers={"X-Owner-Token": "bob"})
            created = await c.post("/projects", json={"project_id": "victim", "topic": "BOB_OVERWRITE"},
                                   headers={"X-Owner-Token": "bob"})
            emit("create_owner_bypass", backend=backend, other_get=denied.status_code,
                 other_create=created.status_code, stored_topic=repo.get_research_context("victim")["topic"],
                 owner=repo.get_state("victim")["owner"])
            mh = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
            def rpc(name, arguments):
                return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}}
            listed = await c.post("/mcp", json=rpc("list_projects", {}), headers=mh)
            read = await c.post("/mcp", json=rpc("get_project", {"project_id": "victim"}), headers=mh)
            write = await c.post("/mcp", json=rpc("update_research_context", {
                "project_id": "victim", "patch": {"topic": "MCP_OVERWRITE"}}), headers=mh)
            env = json.loads(read.json()["result"]["content"][0]["text"])
            listing = json.loads(listed.json()["result"]["content"][0]["text"])
            emit("mcp_owner_bypass", backend=backend, http=read.status_code, ok=env["ok"],
                 leaked_owner=env["data"].get("owner"), projects=len(listing["data"]),
                 write_http=write.status_code, stored_topic=repo.get_research_context("victim")["topic"])
            app.state.llm = ReplyLLM()
            ah = {"X-Owner-Token": "alice"}
            a = await c.post("/projects/victim/messages", json={"text": "question one", "idempotency_key": "same"}, headers=ah)
            b = await c.post("/projects/victim/messages", json={"text": "question one", "idempotency_key": "same"}, headers=ah)
            emit("idempotency", backend=backend, same_tid=a.json()["turn_id"] == b.json()["turn_id"],
                 model_calls=app.state.llm.calls, user_messages=sum(m["role"] == "user" for m in repo.get_messages("victim", 0)))
            newer = await c.post("/projects/victim/messages", json={"text": "question two"}, headers=ah)
            retry = await c.post(f"/projects/victim/turns/{a.json()['turn_id']}/retry", headers=ah)
            messages = repo.get_messages("victim", 0)
            emit("retry_wrong_turn", backend=backend, retry_status=retry.status_code,
                 requested_old_tid=a.json()["turn_id"], newer_tid=newer.json()["turn_id"],
                 actual_last_question=[m["content"] for m in messages if m["role"] == "user"][-1])


async def batch_doc_probe(root, backend):
    repo = JsonRepository(root / "projects") if backend == "json" else SqliteRepository(root / "db.sqlite")
    repo.create("p")
    repo.update_research_context("p", {"title": "Stable", "topic": "OLD_TOPIC", "research_questions": "OLD_RQ"})
    tm = TurnManager(root / "turns-root")
    t = tm.create_turn("p")
    args = copy.deepcopy(_PROPOSAL_ARGS)
    args["ctx"] = {}
    calls = [
        {"id": "s", "name": "update_research_context", "arguments": {"patch": {"topic": "NEW_TOPIC", "research_questions": "NEW_RQ"}}},
        {"id": "d", "name": "compose_proposal", "arguments": args},
    ]
    llm = FakeLLM([[{"content": "", "tool_calls": calls}],
                   [{"delta": "已生成。"}, {"content": "已生成。", "tool_calls": None, "finish_reason": "stop"}]])
    llm.chat = lambda *a, **kw: {"content": ""}
    await loop.run_turn(repo, "p", t.turn_id, "hi", tm, llm, FakeFlyAI())
    events = tm.replay("p", t.turn_id, 0)
    started = next(e for e in events if e.event == "tool_started" and e.data["tool"] == "compose_proposal")
    docs = repo.list_proposals("p")
    accepted = False
    if docs:
        try:
            repo.confirm_proposal("p", docs[-1]["id"])
            accepted = True
        except Exception:
            pass
    emit("same_batch_doc_stale_hash", backend=backend, tool_topic=started.data["args"]["ctx"]["topic"],
         stored_topic=repo.get_research_context("p")["topic"], docs=len(docs),
         old_body="OLD_TOPIC" in docs[-1]["content"] if docs else None,
         accepted=accepted, status=tm.get_turn("p", t.turn_id).status)


async def refs_probe(root):
    repo = JsonRepository(root / "projects")
    repo.create("p")
    repo.update_research_context("p", {"title": "Stable"})
    tm = TurnManager(root / "projects")
    t = tm.create_turn("p")
    class Prices(FakeFlyAI):
        def __init__(self):
            self.n = 0
            self.lock = threading.Lock()
        def search_hotels(self, **kw):
            with self.lock:
                self.n += 1
                p = 11 if self.n <= 2 else 22
            return Envelope(ok=True, data=ItemPage(items=[SearchItem(name="Same Hotel", price=p)], total=1, cheapest=p))
    args = {"departure": "昆明", "destinations": ["大理"], "start_date": "2026-11-01", "duration_days": 3, "budget_total": 10000}
    tool = lambda cid: {"content": "", "tool_calls": [{"id": cid, "name": "compose_itinerary", "arguments": args}]}
    llm = FakeLLM([[tool("a")], [tool("b")], [{"content": "完成。", "tool_calls": None, "finish_reason": "stop"}]])
    await loop.run_turn(repo, "p", t.turn_id, "hi", tm, llm, Prices())
    plans = [repo.get_cached_search("p", c["id"])["result"] for c in repo.list_cached_searches("p") if c["tool"] == "compose_itinerary"]
    first = plans[0]
    item = next(i for i in first["budget"]["items"] if i["name"].startswith("住宿"))
    cid = re.search(r"快照 ([A-Za-z0-9_-]+)", item["source_ref"]).group(1)
    cited = repo.get_cached_search("p", cid)["result"]["items"][0]["price"]
    emit("same_spec_reference_collision", first_unit=first["stays"][0]["nightly_price"], cited_unit=cited, first_amount=item["amount"], plans=len(plans))


def migrate_probes(root):
    repo = JsonRepository(root / "projects")
    repo.create("p")
    repo.append_message("p", "user", "JSON source")
    sqlite = SqliteRepository(root / "db.sqlite")
    sqlite.create("p")
    sqlite.append_message("p", "user", "LIVE TARGET")
    def fail(*args, **kw):
        raise RuntimeError("injected import failure")
    with patch.object(sqlite, "_import_message", fail):
        try:
            migrate_to_sqlite(repo, sqlite, force=True)
        except RuntimeError:
            pass
    emit("migration_no_rollback", final_messages=sqlite.get_messages("p", 0), backup_exists=(root / "db.sqlite.bak.1").exists())
    # Actual legacy cache namespace collision; no injection.
    other = root / "collision"
    j = JsonRepository(other / "projects")
    for pid in ["a", "b"]:
        j.create(pid)
        p = j.root / pid / "search_cache" / "20260105100000_0.json"
        p.write_text(json.dumps({"id": "20260105100000_0", "query": pid, "tool": "search_hotels", "result": {"marker": pid}, "cached_at": "2026-01-05T10:00:00"}), encoding="utf-8")
    s = SqliteRepository(other / "db.sqlite")
    error = ""
    try:
        migrate_to_sqlite(j, s)
    except RuntimeError as exc:
        error = str(exc)
    emit("migration_cache_namespace", failed=bool(error), cache_a=s.get_cached_search("a", "20260105100000_0"),
         cache_b=s.get_cached_search("b", "20260105100000_0"), target_projects=len(s.list_projects()))


async def runtime_probes(root):
    repo = JsonRepository(root / "projects")
    repo.create("p")
    tm = TurnManager(root / "projects")
    class SlowNaming(ReplyLLM):
        def chat(self, *a, **kw):
            time.sleep(0.35)
            return {"content": "审计名称"}
    async def heartbeat():
        before = time.monotonic()
        await asyncio.sleep(0.02)
        return time.monotonic() - before
    t = tm.create_turn("p")
    _, delay = await asyncio.gather(loop.run_turn(repo, "p", t.turn_id, "hi", tm, SlowNaming(), FakeFlyAI()), heartbeat())
    emit("autoname_event_loop_block", heartbeat_expected_ms=20, observed_ms=round(delay * 1000))
    class SlowStream(ReplyLLM):
        def chat_stream(self, messages, **kw):
            yield {"delta": "开始。"}
            time.sleep(0.15)
            yield {"content": "", "tool_calls": [{"id": "after", "name": "estimate_living_cost", "arguments": {"days": 3}}]}
    t = tm.create_turn("p")
    task = asyncio.create_task(loop.run_turn(repo, "p", t.turn_id, "hi", tm, SlowStream(), FakeFlyAI()))
    for _ in range(100):
        if tm.replay("p", t.turn_id, 0):
            break
        await asyncio.sleep(0.002)
    tm.cancel("p", t.turn_id)
    cancel_seq = max(e.seq for e in tm.replay("p", t.turn_id, 0))
    await task
    emit("tools_after_cancel", tools_started_after_cancel=[e.data.get("tool") for e in tm.replay("p", t.turn_id, cancel_seq) if e.event == "tool_started"])
    # Deadline is only checked at iteration boundaries; a single stream exceeds it.
    t = tm.create_turn("p")
    class SlowAnswer(ReplyLLM):
        def chat_stream(self, messages, **kw):
            time.sleep(0.15)
            yield {"content": "完成。", "tool_calls": None, "finish_reason": "stop"}
    before = time.monotonic()
    await loop.run_turn(repo, "p", t.turn_id, "hi", tm, SlowAnswer(), FakeFlyAI(), max_wall_seconds=0.02)
    emit("deadline_not_enforced", deadline_ms=20, elapsed_ms=round((time.monotonic()-before)*1000), status=tm.get_turn("p", t.turn_id).status)
    class NoMap:
        def geocode(self, *a, **kw):
            return Envelope(ok=False, error=ToolError(code="amap_invalid_key", message="No key"))
    for days, people in [(366, 1), (3, 21)]:
        env = compose_itinerary("昆明", ["大理"], "2026-11-01", days, 1000000,
                                researcher_count=people, client=FakeFlyAI(), amap_client=NoMap())
        emit("living_cost_dropped", days=days, people=people, ok=env.ok,
             coverage=env.data.budget.coverage, names=[i.name for i in env.data.budget.items],
             total=env.data.budget.total)


async def main():
    with tempfile.TemporaryDirectory(prefix="fieldwork-audit-20261019-") as tmp:
        root = Path(tmp)
        empty = root / "assets"
        empty.mkdir()
        assets.reload(empty)
        for backend in ["json", "sqlite"]:
            await http_probes(root / (backend + "-http"), backend)
            await batch_doc_probe(root / (backend + "-batch"), backend)
        await refs_probe(root / "refs")
        migrate_probes(root / "migration")
        await runtime_probes(root / "runtime")


if __name__ == "__main__":
    asyncio.run(main())
