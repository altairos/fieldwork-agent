"""住宿与每日往返联合比较；无地址匹配、缺路线和价格口径均显式保留。"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from fieldwork_agent.kernel.aggregation.last_mile import _resolve_date_param
from fieldwork_agent.kernel.aggregation.travel_decisions import match_hotel, result
from fieldwork_agent.kernel.models import Envelope, GeoLocation, ItemPage, SearchItem
from fieldwork_agent.kernel.travel import item_value
from pydantic import BaseModel, ConfigDict, Field, model_validator


class LodgingComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nights: int = Field(ge=1, le=366)
    visit_days: int = Field(ge=0, le=366)
    rooms: int = Field(default=1, ge=1, le=30)
    researcher_count: int = Field(default=1, ge=1, le=100)
    hub_location: str = Field(default="", max_length=150)
    max_commute_minutes: int | None = Field(default=None, ge=0, description="每日往返通勤上限，分钟")
    daily_available_minutes: int | None = Field(default=None, ge=0, le=1440)

    @model_validator(mode="after")
    def validate_visit_days(self):
        if self.visit_days > self.nights + 1:
            raise ValueError("访问天数不能超过驻留日历天数")
        return self


def compare_lodging(items: list[SearchItem], target: GeoLocation,
                    request: LodgingComparisonRequest, date: str, amap_client) -> list[dict]:
    candidates = sorted(items, key=lambda item: item.price if item.price is not None else float("inf"))
    # 分别优先取靠近田野与用户指定枢纽的候选，最多三家，避免全部做路线笛卡尔积。
    selected = []
    for base in ("field", "hub"):
        selected.extend([item for item in candidates if item.extra.get("base_kind", "field") == base][:1])
    selected.extend(item for item in candidates if item not in selected)
    reports = []
    date_param, date_note = _resolve_date_param(date)
    def safe(fn, **kwargs):
        try:
            return fn(**kwargs)
        except Exception:
            return Envelope(ok=False)
    hub_adcode = ""
    if request.hub_location and hasattr(amap_client, "geocode"):
        hub = safe(amap_client.geocode, address=request.hub_location)
        if hub.ok and isinstance(hub.data, GeoLocation) and not hub.data.ambiguous:
            hub_adcode = hub.data.adcode
    for item in selected[:3]:
        missing, failures = [], []
        report = {"name": item.name, "base_kind": item.extra.get("base_kind", "field"),
            "nightly_price": item.price, "evidence": item.evidence.model_dump(),
            "assumptions": {"nights": request.nights, "visit_days": request.visit_days,
                "rooms": request.rooms, "researcher_count": request.researcher_count},
            "quote_coverage": "estimate", "match_status": "unknown"}
        report["price_basis_assumption"] = "按展示价为单间每晚的情景估算，口径待核实"
        price_basis = item_value(item, "price_basis")
        room_total = item.price if price_basis == "total_per_room" else item.price * request.nights if item.price is not None else None
        if price_basis in ("nightly_per_room", "total_per_room"):
            report["price_basis_assumption"] = "按来源声明的单间计价口径计算，实际房态待核实"
        address = item_value(item, "address")
        if not address or len(str(address)) < 5 or target.ambiguous or not target.location:
            report["constraints"] = result([], ["住宿地址及唯一访问点定位"])
            reports.append(report)
            continue
        search = safe(amap_client.text_search, keywords=item.name, city=item.extra.get("base_city") or target.city, page_size=5)
        expected_admin = hub_adcode if item.extra.get("base_kind") == "hub" else target.adcode
        matched, reason = match_hotel(item.model_dump(), search.data if search.ok and isinstance(search.data, ItemPage) else ItemPage(), expected_admin)
        report["match_basis"] = reason
        if matched is None:
            report["constraints"] = result([], ["唯一住宿地点匹配"])
            reports.append(report)
            continue
        report["match_status"] = "matched"
        report["geo"] = matched.model_dump()
        directions = [(matched, target), (target, matched)]
        def route(pair):
            a, b = pair
            return safe(amap_client.direction_transit, origin=a.location, destination=b.location,
                city=a.city, cityd=b.city, date=date_param)
        with ThreadPoolExecutor(max_workers=2) as pool:
            envs = [f.result() for f in [pool.submit(copy_context().run, route, pair) for pair in directions]]
        pages = [env.data if env.ok and isinstance(env.data, ItemPage) else ItemPage() for env in envs]
        pairs = [(a, b) for a in pages[0].items[:3] for b in pages[1].items[:3]]
        def daily(pair):
            durations = [i.schedule.duration_minutes if i.schedule else None for i in pair]
            return sum(durations) if all(d is not None for d in durations) else None
        permitted = [pair for pair in pairs if request.max_commute_minutes is None or daily(pair) is None
                     or daily(pair) <= request.max_commute_minutes]
        if not pairs:
            missing.append("双向通勤路线")
        elif not permitted:
            if date_param:
                failures.append("往返通勤超过用户上限")
            else:
                missing.append("参考路线超过通勤上限，目标日期仍需核实")
        best = min(permitted, key=lambda pair: sum(i.price for i in pair)
                   if all(i.price is not None for i in pair) else float("inf"), default=None)
        commute = daily(best) if best else None
        daily_cost = sum(i.price for i in best) * request.researcher_count if best and all(i.price is not None for i in best) else None
        if commute is None:
            missing.append("双向通勤时长")
        if daily_cost is None:
            missing.append("双向通勤费用")
        missing.append("连续房态、价格口径与现场进入条件")
        if date_note or not date_param:
            missing.append("目标日期路线核验")
        if target.level in ("", "国家", "省", "市", "区县"):
            missing.append("具体访问点")
        report.update({"commute_minutes_per_visit_day": commute,
            "commute_cost_per_visit_day": daily_cost,
            "cost_estimate": round(room_total * request.rooms + daily_cost * request.visit_days, 2)
                if room_total is not None and daily_cost is not None else None,
            "research_minutes_per_visit_day": max(0, request.daily_available_minutes - commute)
                if request.daily_available_minutes is not None and commute is not None else None,
            "routes": [i.model_dump() for i in best] if best else [],
            "constraints": result(failures, missing)})
        reports.append(report)
    return sorted(reports, key=lambda r: (r["constraints"]["status"] == "failed",
        r.get("cost_estimate") if r.get("cost_estimate") is not None else float("inf")))
