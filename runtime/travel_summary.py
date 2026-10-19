"""按决策字段压缩旅行结果；完整返回保留在事件/快照中。"""
import json
from typing import Any

TRAVEL_TOOLS = {"profile_field_site", "compose_itinerary", "plan_last_mile", "analyze_fieldwork_window",
    "search_flights", "search_trains", "search_hotels", "search_marriott_hotels",
    "search_marriott_packages", "search_pois", "search_keyword", "search_ai",
    "amap_geocode", "amap_regeocode", "amap_text_search", "amap_around_search",
    "amap_direction_transit", "amap_weather"}


def brief_item(item: dict) -> dict:
    out = {k: v for k, v in item.items() if k in ("name", "price", "price_raw", "rating") and v is not None}
    out.setdefault("name", item.get("type") or item.get("title") or "")
    extra = item.get("extra") or {}
    info = extra.get("info") if isinstance(extra.get("info"), dict) else {}
    details = {**info, **extra}
    selected = ("id", "hotelId", "shId", "address", "location", "latitude", "longitude", "adcode", "type", "distance",
        "walking_distance", "facility_kind", "roomType", "roomName", "skuId", "checkIn", "checkOut",
        "rating", "rate", "star", "brandName", "origin", "destination", "constraints", "match_status")
    selected_details = {k: details[k] for k in selected if details.get(k) not in (None, "", [])}
    if selected_details:
        out["details"] = selected_details
    schedule = item.get("schedule")
    if isinstance(schedule, dict):
        out["schedule"] = {k: v for k, v in schedule.items() if k != "segments" and v not in (None, "", [])}
        # 分段仅摘实际站点与运营时间，避免把路径坐标放入上下文。
        segments = schedule.get("segments") or details.get("segments") or []
        legs = []
        for seg in segments[:4]:
            if not isinstance(seg, dict):
                continue
            lines = (seg.get("bus") or {}).get("buslines") or []
            legs.append({k: seg[k] for k in ("depTime", "arrTime", "depDate", "arrDate", "depAirport", "arrAirport", "depStation", "arrStation") if k in seg})
            legs.extend({k: line[k] for k in ("name", "departure_stop", "arrival_stop", "start_time", "end_time", "station_start_time", "station_end_time") if k in line} for line in lines[:2])
        if legs:
            out["schedule"]["segments"] = legs
        if len(segments) > 4:
            out["schedule"]["omitted_segments"] = len(segments) - 4
    if isinstance(item.get("evidence"), dict) and item["evidence"].get("source_ref"):
        out["evidence"] = item["evidence"]
    for key in ("constraints", "connection", "selection_reason"):
        if key in item:
            out[key] = item[key]
    return out


def compact(value: Any, depth: int = 0) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if depth > 10:
        return {"omitted": True}
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:500] + "…"
    if isinstance(value, list):
        return [compact(v, depth + 1) for v in value[:3]]
    if isinstance(value, dict):
        if "name" in value and "price" in value and "extra" in value:
            return compact(brief_item(value), depth + 1)
        if isinstance(value.get("items"), list):
            return {"total": value.get("total"), "returned_count": len(value["items"]),
                "omitted_count": max(0, len(value["items"]) - 3),
                "raw_count": value.get("raw_count"), "entity_count": value.get("entity_count"),
                "items": [compact(brief_item(it), depth + 1) for it in value["items"][:3] if isinstance(it, dict)]}
        result = {k: compact(v, depth + 1) for k, v in value.items()
                  if k not in ("pic_url", "jump_url", "polyline", "inputs_hash")}
        omitted = {k: len(v) - 3 for k, v in value.items() if isinstance(v, list) and len(v) > 3}
        if omitted:
            result["omitted_counts"] = omitted
        return result
    return value


def bounded_json(summary: dict, limit: int) -> str:
    """删整条候选/缩短长文本，始终返回合法 JSON，并明确提示省略。"""
    def dump():
        return json.dumps(summary, ensure_ascii=False, default=str)

    while len(dump()) > limit:
        lists: list[list] = []
        strings: list[tuple[dict, str]] = []

        def visit(obj, lists=lists, strings=strings):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if isinstance(value, str) and len(value) > 100:
                        strings.append((obj, key))
                    elif isinstance(value, list) and len(value) > 1 and key not in ("missing_fields", "uncovered", "failures"):
                        lists.append(value)
                    visit(value)
            elif isinstance(obj, list):
                for value in obj:
                    visit(value)

        visit(summary)
        if lists:
            max(lists, key=lambda seq: len(json.dumps(seq, default=str))).pop()
        elif strings:
            parent, key = max(strings, key=lambda pair: len(pair[0][pair[1]]))
            parent[key] = parent[key][:max(80, len(parent[key]) // 2)] + "…"
        else:
            return json.dumps({"tool": summary.get("tool"), "ok": summary.get("ok"),
                "summary_omitted": True, "instruction": "结果过长，需查看完整工具事件；缺失条件不能视为已满足。"}, ensure_ascii=False)
        summary["summary_omitted"] = True
    return dump()
