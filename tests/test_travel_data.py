"""旅行证据经过适配、聚合和摘要仍能支撑判断，且未知不会被升级。"""
import json
from pathlib import Path

from fieldwork_agent.kernel.aggregation.itinerary import _candidates
from fieldwork_agent.kernel.aggregation.window import _extract_festivals, _score_windows
from fieldwork_agent.kernel.models import Envelope, FestivalInfo, ItemPage, PriceCurve, SearchItem
from fieldwork_agent.kernel.sources.amap import AMapClient, _transits_page
from fieldwork_agent.kernel.sources.flyai import _page_envelope
from fieldwork_agent.kernel.travel import stamp_evidence, visible_page
from fieldwork_agent.runtime.loop import _tool_content


def test_real_response_shape_keeps_schedule_through_itinerary_and_summary():
    raw = json.loads((Path(__file__).parent / "fixtures/flyai_responses/flight_journeys.json").read_text(encoding="utf-8"))
    env = stamp_evidence(_page_envelope(raw), "flyai", {"dep_date": "2026-11-01"})
    candidates = _candidates(env.data.items, "flight")
    assert candidates[0]["schedule"]["arrival_place"] == "西昌青山"
    assert candidates[0]["schedule"]["arr_time"] == "10:05"
    assert len(candidates[1]["schedule"]["segments"]) == 2
    assert candidates[0]["evidence"]["source_ref"]
    summary = json.loads(_tool_content("compose_itinerary", Envelope(ok=True, data={"legs": [{"chosen": candidates[0]}]})))
    assert summary["fields"]["legs"][0]["chosen"]["schedule"]["arr_time"] == "10:05"


def test_transit_keeps_boarding_stop_operating_hours_and_walking():
    line = {"name": "乡村公交", "departure_stop": {"name": "车站", "id": "s1"}, "station_end_time": "1800"}
    env = _transits_page({"route": {"origin": "1,2", "destination": "2,3", "transits": [{
        "cost": "8", "duration": "3600", "walking_distance": "500", "segments": [{"bus": {"buslines": [line]}}]}]}})
    item = env.data.items[0]
    assert item.schedule.segments[0]["bus"]["buslines"][0]["station_end_time"] == "1800"
    summary = json.loads(_tool_content("amap_direction_transit", env))
    assert summary["top_items"][0]["details"]["walking_distance"] == 500
    assert "1800" in json.dumps(summary)


def test_entity_and_offer_dedup_does_not_merge_distinct_rooms_or_same_name_places():
    a = SearchItem(name="村舍", price=100, extra={"hotelId": "h1", "roomType": "单人"})
    b = a.model_copy(deep=True)
    c = SearchItem(name="村舍", price=100, extra={"hotelId": "h1", "roomType": "双人"})
    d = SearchItem(name="村舍", price=120, extra={"hotelId": "h2"})
    page = visible_page([a, b, c, d], 5)
    assert page.raw_count == 4 and page.total == 3 and page.entity_count == 2
    assert {i.extra.get("roomType") for i in page.items if i.extra.get("hotelId") == "h1"} == {"单人", "双人"}
    assert visible_page([SearchItem(name="卫生院", extra={"address": "甲村"}), SearchItem(name="卫生院", extra={"address": "乙村"})], 5).total == 2


def test_visible_price_summary_does_not_use_hidden_cheapest():
    page = visible_page([SearchItem(name="已展示", price=200), SearchItem(name="隐藏", price=10)], 1)
    assert page.cheapest == 200 and page.total == 2


def test_cached_evidence_keeps_original_fetch_time_and_immutable_copy(monkeypatch):
    client = AMapClient("test")
    monkeypatch.setattr(client, "_get", lambda *a: {"infocode": "10000", "pois": [{"id": "p1", "name": "集市", "location": "1,2"}]})
    first = client.text_search("集市", "县")
    second = client.text_search("集市", "县")
    assert first.evidence.fetched_at == second.evidence.fetched_at
    assert not first.evidence.cache_hit and second.evidence.cache_hit
    assert first.data.items[0].evidence.source_ref == second.data.items[0].evidence.source_ref
    second.data.items[0].extra["location"] = "bad"
    assert client.text_search("集市", "县").data.items[0].extra["location"] == "1,2"


def test_large_result_summary_is_valid_bounded_json():
    env = Envelope(ok=True, data=ItemPage(items=[SearchItem(name="甲" * 2000, price=10, extra={"address": "乙" * 6000}) for _ in range(20)]))
    text = _tool_content("search_hotels", env)
    assert len(text) <= 6000
    parsed = json.loads(text)
    assert parsed["ok"] and parsed["summary_omitted"]


def test_model_cannot_promote_tourism_dates_into_confirmed_festival():
    class LLM:
        def chat(self, **kw):
            return {"content": '[{"name":"火把节","approx_start":"2026-11-01","approx_end":"2026-11-02","date_status":"verified_source"}]'}
    festivals, note = _extract_festivals(LLM(), "甲县", "2026-11-01", "2026-11-10", ["火把节旅游套餐"])
    assert festivals[0].approx_start == "" and festivals[0].date_status == "unverified"
    assert note and "不参与" in note
    curve = PriceCurve(dates=["2026-11-01"], flight_min=[100], hotel_min=[100])
    windows = _score_windows(curve, festivals, "2026-11-01", "2026-11-10", 2, 5000, 7)
    assert all(w.evidence["festival_score"] == 0 for w in windows)
    f = FestivalInfo(name="未验证", approx_start="2026-11-01", approx_end="2026-11-02")
    assert _score_windows(curve, [f], "2026-11-01", "2026-11-10", 2, 5000, 7)[0].evidence["festival_score"] == 0
