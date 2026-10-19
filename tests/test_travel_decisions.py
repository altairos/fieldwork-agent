from datetime import date

from fieldwork_agent.kernel.aggregation.itinerary import compose_itinerary
from fieldwork_agent.kernel.aggregation.lodging import LodgingComparisonRequest, compare_lodging
from fieldwork_agent.kernel.aggregation.travel_decisions import (
    TravelConstraints,
    arrival_datetime,
    candidate_constraints,
)
from fieldwork_agent.kernel.models import Envelope, GeoLocation, ItemPage, Schedule, SearchItem


def _route(minutes=40, price=8):
    return Envelope(ok=True, data=ItemPage(items=[SearchItem(name="公交", price=price,
        extra={"walking_distance": 100}, schedule=Schedule(duration_minutes=minutes, segments=[{
            "walking": {"duration": "300"}, "bus": {"buslines": [{
                "name": "村际车", "station_start_time": "0600", "station_end_time": "1800", "duration": "2100"}]}}]))], cheapest=price))


class Geo:
    def __init__(self):
        self.addresses = []

    def geocode(self, address, city=""):
        self.addresses.append(address)
        return Envelope(ok=True, data=GeoLocation(location="102,25", city="甲县", level="兴趣点"))

    def direction_transit(self, **kw):
        return _route()


class Supply:
    def transport(self, kw, time, place, price):
        return Envelope(ok=True, data=ItemPage(items=[SearchItem(name=place, price=price,
            schedule=Schedule(dep_time="06:00", arr_time=time, arr_date=kw["dep_date"],
                dep_date=kw["dep_date"], timezone="Asia/Shanghai", departure_place="出发站", arrival_place=place))]))

    def search_flights(self, **kw):
        return self.transport(kw, "22:00", "甲机场", 100)

    def search_trains(self, **kw):
        return self.transport(kw, "10:00", "甲火车站", 150)

    def search_hotels(self, **kw):
        return Envelope(ok=True, data=ItemPage(items=[SearchItem(name="民宿", price=100)]))


def test_cheaper_late_arrival_rejected_and_actual_arrival_station_used():
    geo = Geo()
    env = compose_itinerary("出发城市", ["访问村"], date.today().isoformat(), 2, 5000,
        client=Supply(), amap_client=geo, constraints={"connection_buffer_minutes": 30})
    assert env.ok
    leg = env.data.legs[0]
    assert leg["chosen"]["mode"] == "train" and leg["chosen"]["price"] == 150
    assert leg["rejected_candidates"][0]["constraints"]["status"] == "failed"
    assert "甲机场" in geo.addresses and "甲火车站" in geo.addresses
    assert geo.addresses[0] == "甲机场"  # 去程接驳从实际到达枢纽开始；返程仍可定位目的地城市
    assert leg["chosen"]["connection"]["status"] == "unknown"  # 运营窗口不是实际可乘承诺


def test_unknown_cross_day_or_timezone_does_not_pass_latest_arrival():
    unknown = {"schedule": {"arr_time": "08:00"}}
    assert candidate_constraints(unknown, "2026-11-01", TravelConstraints(latest_arrival_time="18:00"))["status"] == "unknown"
    assert arrival_datetime({"arr_time": "2026-11-01T18:00:00Z"}, "2026-11-01").date().isoformat() == "2026-11-02"
    assert candidate_constraints({"schedule": {"arr_time": "2026-11-01T18:00:00Z"}}, "2026-11-01", TravelConstraints(latest_arrival_time="18:00"))["status"] == "failed"


def test_explicit_research_activity_changes_nights_and_hash():
    a = compose_itinerary("出发", ["甲村", "乙村"], date.today().isoformat(), 5, 5000,
        client=Supply(), amap_client=Geo(), night_allocation=[1, 3])
    b = compose_itinerary("出发", ["甲村", "乙村"], date.today().isoformat(), 5, 5000,
        client=Supply(), amap_client=Geo())
    assert [s["nights"] for s in a.data.stays] == [1, 3]
    assert a.data.inputs_hash != b.data.inputs_hash
    assert not compose_itinerary("出发", ["甲村"], date.today().isoformat(), 5, 5000,
        client=Supply(), night_allocation=[2]).ok


class HotelGeo:
    def text_search(self, keywords, **kw):
        near = keywords == "近舍"
        return Envelope(ok=True, data=ItemPage(items=[SearchItem(name=keywords, extra={
            "id": "p2" if near else "p1", "address": "甲县近路二号" if near else "甲县远路一号",
            "location": "2,2" if near else "1,1", "cityname": "甲县", "adcode": "1"})]))

    def direction_transit(self, origin, destination, **kw):
        near = "2,2" in (origin, destination)
        return _route(10 if near else 120, 2 if near else 40)


def test_joint_lodging_comparison_separates_cost_and_time():
    items = [SearchItem(name="远舍", price=100, extra={"address": "甲县远路一号"}),
             SearchItem(name="近舍", price=140, extra={"address": "甲县近路二号"})]
    target = GeoLocation(location="3,3", city="甲县", adcode="1", level="村庄")
    request = LodgingComparisonRequest(nights=6, visit_days=6, daily_available_minutes=480, max_commute_minutes=60)
    rows = compare_lodging(items, target, request, date.today().isoformat(), HotelGeo())
    assert rows[0]["name"] == "近舍"
    assert rows[0]["cost_estimate"] == 864 and rows[0]["commute_minutes_per_visit_day"] == 20
    assert rows[0]["research_minutes_per_visit_day"] == 460
    assert rows[0]["constraints"]["status"] == "unknown"
    assert rows[1]["constraints"]["status"] == "failed"
    assert rows[0]["quote_coverage"] == "estimate"


def test_same_name_without_address_cannot_be_joined_or_assigned_research_hours():
    rows = compare_lodging([SearchItem(name="近舍", price=140)], GeoLocation(location="3,3"),
        LodgingComparisonRequest(nights=6, visit_days=6), date.today().isoformat(), HotelGeo())
    assert rows[0]["match_status"] == "unknown"
    assert "research_minutes_per_visit_day" not in rows[0]
