import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fieldwork_agent.kernel.source_requests import SourceBudget
from fieldwork_agent.kernel.sources.amap import AMapClient
from fieldwork_agent.kernel.sources.flyai import FlyAIClient


def _fly_payload():
    return {"status": 0, "data": {"itemList": [{"title": "住宿候选", "price": "100"}]}}


def test_parallel_cross_source_requests_share_atomic_budget(monkeypatch):
    fly, amap = FlyAIClient("f"), AMapClient("a")
    seen = []
    lock = threading.Lock()
    def outgoing(source):
        with lock:
            seen.append(source)
        return _fly_payload() if source == "fly" else {"infocode": "10000", "pois": []}
    monkeypatch.setattr(fly, "_run_command", lambda args: outgoing("fly"))
    monkeypatch.setattr(amap, "_get", lambda *args: outgoing("amap"))
    budget = SourceBudget(limit=5, namespace="project")
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(budget.wrap(lambda n=n: fly.search_keyword(f"住宿{n}") if n % 2 else amap.text_search(f"市场{n}"))) for n in range(12)]
        results = [f.result() for f in futures]
    assert len(seen) == 5 and sum(r.ok for r in results) == 5
    assert budget.snapshot()["actual_requests"] == 5
    assert budget.snapshot()["blocked"] == 7
    assert all(r.ok or r.error.code == "source_budget_exceeded" for r in results)


def test_concurrent_identical_requests_coalesce_and_keep_fetch_time(monkeypatch):
    client = FlyAIClient("f")
    calls = []
    def fetch(args):
        calls.append(args)
        time.sleep(0.06)
        return _fly_payload()
    monkeypatch.setattr(client, "_run_command", fetch)
    budget = SourceBudget(namespace="same-project")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: budget.wrap(client.search_keyword)("民宿"), range(6)))
    assert len(calls) == 1
    assert len({r.evidence.fetched_at for r in results}) == 1
    assert budget.snapshot()["coalesced"] > 0
    cached = budget.wrap(client.search_keyword)("民宿")
    assert cached.evidence.cache_hit and cached.evidence.fetched_at == results[0].evidence.fetched_at
    assert budget.snapshot()["actual_requests"] == 1


def test_cache_partitions_project_credentials_and_dates(monkeypatch):
    client = FlyAIClient("first")
    seen = []
    monkeypatch.setattr(client, "_run_command", lambda args: seen.append(args) or _fly_payload())
    a, b = SourceBudget(namespace="project-a"), SourceBudget(namespace="project-b")
    a.wrap(client.search_hotels)("甲县", check_in="2028-01-01")
    a.wrap(client.search_hotels)("甲县", check_in="2028-01-01")
    b.wrap(client.search_hotels)("甲县", check_in="2028-01-01")
    a.wrap(client.search_hotels)("甲县", check_in="2028-01-02")
    client.api_key = "second"
    a.wrap(client.search_hotels)("甲县", check_in="2028-01-01")
    assert len(seen) == 4
    assert all("first" not in str(key) and "second" not in str(key) for key in client._cache)


def test_failure_shared_only_inflight_and_retried_later(monkeypatch):
    client = FlyAIClient("f")
    seen = []
    def fail(args):
        seen.append(args)
        time.sleep(0.04)
        return {"status": -1, "error": "timeout"}
    monkeypatch.setattr(client, "_run_command", fail)
    b = SourceBudget(namespace="p")
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda _: b.wrap(client.search_keyword)("住宿"), range(4)))
    assert all(not r.ok for r in result) and len(seen) == 1
    assert not b.wrap(client.search_keyword)("住宿").ok and len(seen) == 2


def test_cancel_stops_new_outbound_calls(monkeypatch):
    client = FlyAIClient("f")
    monkeypatch.setattr(client, "_run_command", lambda args: (_ for _ in ()).throw(AssertionError("must not call")))
    result = SourceBudget(cancelled=lambda: True).wrap(client.search_keyword)("住宿")
    assert not result.ok and result.error.code == "source_budget_exceeded"
    assert client.stats.get("actual_requests", 0) == 0


def test_invalid_ip_stops_repeated_provider_requests_within_task(monkeypatch):
    client = AMapClient("f")
    calls = []
    monkeypatch.setattr(client, "_get", lambda *args: calls.append(args) or {"infocode": "10005", "info": "INVALID_USER_IP"})
    budget = SourceBudget(namespace="project")
    first = budget.wrap(client.geocode)("甲村")
    second = budget.wrap(client.geocode)("乙村")
    assert first.error.code == second.error.code == "amap_ip_denied"
    assert len(calls) == 1 and budget.snapshot()["disabled_sources"] == {"amap": "amap_ip_denied"}
    assert budget.snapshot()["failed"] == 1 and budget.snapshot()["blocked"] == 1


def test_last_mile_preserves_ip_error_instead_of_relabelling_unknown_place(monkeypatch):
    from fieldwork_agent.kernel.aggregation.last_mile import plan_last_mile
    client = AMapClient("f")
    monkeypatch.setattr(client, "_get", lambda *args: {"infocode": "10005", "info": "INVALID_USER_IP"})
    env = plan_last_mile("甲市", "甲村", amap_client=client)
    assert not env.ok and env.error.code == "amap_ip_denied" and "IP" in env.error.hint


def test_runtime_budget_covers_multiple_aggregate_tool_calls(monkeypatch):
    from fieldwork_agent.kernel.models import Envelope
    from fieldwork_agent.runtime.loop import run_tool_loop

    client = FlyAIClient("f")
    seen = []
    monkeypatch.setattr(client, "_run_command", lambda args: seen.append(args) or _fly_payload())
    class LLM:
        turns = 0
        def chat(self, *a, **kw):
            self.turns += 1
            if self.turns == 1:
                return {"tool_calls": [{"id": str(n), "name": "aggregate", "arguments": {"group": n}} for n in (1, 2)]}
            return {"content": "已有结果"}
    def dispatch(name, args):
        items = [client.search_keyword(f"住宿{args['group']}_{i}") for i in range(15)]
        return Envelope(ok=True, data={"successes": sum(i.ok for i in items)})
    budget = SourceBudget(namespace="p")
    result = run_tool_loop(LLM(), [], dispatch=budget.wrap(dispatch), tools=[])
    assert len(seen) == 20 and budget.snapshot()["blocked"] == 10
    assert result.tool_events[1]["source_usage"]["actual_requests"] == 20
