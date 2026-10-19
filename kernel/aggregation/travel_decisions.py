"""旅行候选约束与地点关联；确定性规则不把未知视为已满足。"""
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fieldwork_agent.kernel.models import GeoLocation, ItemPage
from pydantic import BaseModel, ConfigDict, Field


class TravelConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_duration_minutes: int | None = Field(default=None, ge=0)
    max_walking_meters: int | None = Field(default=None, ge=0)
    latest_arrival_time: str = Field(default="", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$|^$", description="大交通到达枢纽的最晚当地时间；不等于最终访问点到达时间")
    connection_buffer_minutes: int | None = Field(default=None, ge=0, le=1440)
    preference: Literal["cost", "time"] = "cost"


def result(failures: list[str], missing: list[str]) -> dict:
    return {"status": "failed" if failures else "unknown" if missing else "passed",
            "failures": list(dict.fromkeys(failures)), "missing_fields": list(dict.fromkeys(missing))}


def clock_minutes(value) -> int | None:
    match = re.fullmatch(r"([0-2]\d):?([0-5]\d)", str(value or ""))
    if not match or int(match[1]) > 23:
        return None
    return int(match[1]) * 60 + int(match[2])


def arrival_datetime(schedule: dict, query_date: str) -> datetime | None:
    """仅使用完整日期/偏移和明确时区；不猜跨日，不按服务器时区解释。"""
    value = str(schedule.get("arr_time") or "")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        minutes = clock_minutes(value)
        if minutes is None:
            return None
        date = schedule.get("arr_date")
        offset = schedule.get("arrival_day_offset")
        if not date and offset is not None:
            try:
                date = (datetime.fromisoformat(schedule.get("dep_date") or query_date) + timedelta(days=offset)).date().isoformat()
            except (ValueError, TypeError):
                return None
        if not date:
            return None
        try:
            dt = datetime.fromisoformat(str(date)).replace(hour=minutes // 60, minute=minutes % 60)
        except ValueError:
            return None
    if dt.tzinfo is None:
        tz = schedule.get("timezone")
        if tz in ("Asia/Shanghai", "Asia/Hong_Kong", "+08:00", "UTC+08:00"):
            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        elif tz in ("UTC", "Z", "+00:00"):
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            return None
    return dt.astimezone(timezone(timedelta(hours=8)))


def candidate_constraints(candidate: dict, date: str, constraints: TravelConstraints) -> dict:
    schedule = candidate.get("schedule") or {}
    failures, missing = [], []
    if schedule.get("dep_date") and schedule["dep_date"][:10] != date:
        failures.append("来源出发日期与行程不匹配")
    if constraints.max_duration_minutes is not None:
        duration = schedule.get("duration_minutes")
        if duration is None:
            missing.append("全程时长")
        elif duration > constraints.max_duration_minutes:
            failures.append("超过可接受交通时长")
    if constraints.latest_arrival_time:
        arrival = arrival_datetime(schedule, date)
        if arrival is None:
            missing.append("到达日期/时区")
        elif arrival.date().isoformat() != date or arrival.hour * 60 + arrival.minute > clock_minutes(constraints.latest_arrival_time):
            failures.append("超过最晚到达时间")
    return result(failures, missing)


def transit_connection(candidate: dict, page: ItemPage | None, date: str,
                       constraints: TravelConstraints, route_date: str,
                       anchored: bool) -> tuple[dict, ItemPage | None]:
    """运营时间只能验证窗口约束，不能证明座位或精确发车时刻。"""
    schedule = candidate.get("schedule") or {}
    arrival = arrival_datetime(schedule, date)
    records = []
    viable = []
    for option in page.items if page else []:
        failures, missing = [], []
        if not anchored:
            missing.append("实际到达枢纽/访问点定位")
        if arrival is None:
            missing.append("到达日期/时区")
        elif route_date != arrival.date().isoformat():
            missing.append("到达日期对应的接驳")
        walk = option.extra.get("walking_distance")
        if constraints.max_walking_meters is not None:
            if walk is None:
                missing.append("步行距离")
            elif walk > constraints.max_walking_meters:
                failures.append("接驳步行距离超过约束")
        offset = constraints.connection_buffer_minutes
        if offset is None:
            missing.append("出站换乘缓冲")
            offset = 0
        lines_checked = 0
        for seg in option.schedule.segments if option.schedule else []:
            walking = seg.get("walking") or {}
            try:
                offset += float(walking.get("duration") or 0) / 60
            except (ValueError, TypeError):
                missing.append("接驳步行时间")
            lines = (seg.get("bus") or {}).get("buslines") or []
            # 同一段的多条 busline 是替代选项，不能错误地累加为多次乘车。
            if len(lines) != 1:
                missing.append("唯一可接续公交线路")
                continue
            line = lines[0]
            start, end = clock_minutes(line.get("station_start_time")), clock_minutes(line.get("station_end_time"))
            if start is None or end is None:
                missing.append("上车站首末班时间")
            elif arrival is not None and route_date == arrival.date().isoformat() and anchored:
                ready = arrival + timedelta(minutes=offset)
                value = ready.hour * 60 + ready.minute
                inside = (start <= value <= end) if start <= end else (value >= start or value <= end)
                if not inside and start <= end and value > end:
                    failures.append("到达上车站时不在运营窗口内")
                elif not inside:
                    missing.append("等待首班及后续接续时间")
                lines_checked += 1
            try:
                offset += float(line.get("duration") or 0) / 60
            except (TypeError, ValueError):
                missing.append("公交分段时长")
        if not lines_checked:
            missing.append("接驳运营时间核验")
        missing.append("实际班次等候时间与可乘状态")
        checked = result(failures, missing)
        copied = option.model_copy(deep=True)
        copied.extra["constraints"] = checked
        records.append(copied)
        if not failures:
            viable.append(copied)
    if not records:
        return result([], ["接驳方案"]), page
    prices = [i.price for i in viable if i.price is not None]
    bounded = ItemPage(items=viable, total=len(viable), cheapest=min(prices) if prices else None,
                       dearest=max(prices) if prices else None)
    if not viable:
        return result(["全部接驳方案不满足约束"], []), bounded
    return result([], [v for i in viable for v in i.extra["constraints"]["missing_fields"]]), bounded


def match_hotel(item: dict, page: ItemPage, adcode: str) -> tuple[GeoLocation | None, str]:
    extra = item.get("extra") or {}
    info = extra.get("info") if isinstance(extra.get("info"), dict) else {}
    address = str(extra.get("address") or info.get("address") or "")
    def normalize(v):
        return re.sub(r"[\W_]", "", str(v))
    if len(normalize(address)) < 5:
        return None, "住宿缺少可核对地址，不能仅凭同名关联"
    if not adcode:
        return None, "住宿所在行政区缺少独立定位依据"
    def same_admin(value):
        value = str(value or "")
        prefix = adcode[:2] if adcode.endswith("0000") else adcode[:4] if adcode.endswith("00") else adcode
        return bool(value) and value.startswith(prefix)
    matches = [poi for poi in page.items if normalize(poi.name) == normalize(item.get("name"))
        and normalize(poi.extra.get("address")) == normalize(address)
        and same_admin(poi.extra.get("adcode"))
        and poi.extra.get("location")]
    if len(matches) != 1:
        return None, "住宿与地理实体匹配存在歧义或不足"
    p = matches[0]
    return GeoLocation(location=p.extra["location"], adcode=str(p.extra.get("adcode") or ""),
                       city=str(p.extra.get("cityname") or ""), level="兴趣点",
                       formatted_address=str(p.extra.get("address") or ""),
                       evidence=p.evidence), "名称、地址和行政区匹配"
