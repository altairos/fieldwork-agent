"""旅行/地理证据归一与保守去重；不做网络或 repository I/O。"""
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem, SourceEvidence


def finite_price(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def item_value(item: SearchItem, *keys: str) -> Any:
    info = item.extra.get("info")
    for obj in (item.extra, info if isinstance(info, dict) else {}):
        for key in keys:
            value = obj.get(key)
            if value not in (None, "", []):
                return value
    return None


def stamp_evidence(env: Envelope, source: str, conditions: dict,
                   *, cache_hit: bool = False) -> Envelope:
    """在真实取数后绑定条件；复用时保留原取数时间，不改写成实时采样。"""
    if not env.ok:
        return env
    fetched_at = (env.evidence.fetched_at if env.evidence else "") or datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(json.dumps([source, conditions, fetched_at],
        ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:24]
    evidence = SourceEvidence(source=source, query_conditions=conditions,
        fetched_at=fetched_at, cache_hit=cache_hit, source_ref=f"{source}:{digest}")
    env.evidence = evidence
    data = env.data
    if hasattr(data, "evidence"):
        data.evidence = evidence.model_copy(deep=True)
    if isinstance(data, ItemPage):
        for item in data.items:
            item.evidence = evidence.model_copy(deep=True)
            item.evidence.source_item_id = str(item_value(item, "id", "hotelId", "itemId", "skuId", "journeyId") or "")
            item.evidence.source_ref += ":" + item.evidence.source_item_id + ":" + hashlib.sha256(
                json.dumps([item.name, item.price, item.extra, item.schedule.model_dump() if item.schedule else None],
                    sort_keys=True, default=str).encode()).hexdigest()[:16]
            if conditions.get("command") in ("search-flight", "search-train"):
                item.evidence.missing_fields = [key for key in ("dep_time", "arr_time", "departure_place", "arrival_place")
                    if not item.schedule or not getattr(item.schedule, key)]
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                row["evidence"] = evidence.model_dump()
                row["evidence"]["source_updated_at"] = str(row.get("reporttime") or "")
    return env


def entity_key(item: SearchItem) -> str | None:
    identity = item_value(item, "hotelId", "id", "itemId", "journeyId")
    if identity:
        return json.dumps([item.evidence.source, identity], ensure_ascii=False, sort_keys=True)
    address, location = item_value(item, "address"), item_value(item, "location")
    if address or location:
        return json.dumps([item.name.strip(), item_value(item, "adcode", "cityname"), address, location],
            ensure_ascii=False, sort_keys=True, default=str)
    return None  # 单凭同名不能认定同一家或同一个村


def dedupe_items(items: list[SearchItem]) -> tuple[list[SearchItem], int]:
    """只合并有实体依据且同报价、班次和供给条件的重复项，保留全部证据引用。"""
    result: list[SearchItem] = []
    seen: dict[str, SearchItem] = {}
    entities: set[str] = set()
    for index, item in enumerate(items):
        entity = entity_key(item)
        entities.add(entity or f"unknown:{index}")
        # 保留房型/套餐/日期/退改等未知字段，宁可少合并，也不误删不同商品。
        extra = {k: v for k, v in item.extra.items() if k not in ("facility_kind", "matched_queries")}
        dates = {k: v for k, v in item.evidence.query_conditions.items()
                 if k in ("check_in", "check_out", "check-in-date", "check-out-date", "dep_date", "date")}
        key = json.dumps([entity, item.price, item.price_raw, item.schedule.model_dump() if item.schedule else None,
                          extra, dates], ensure_ascii=False, sort_keys=True, default=str)
        if entity and key in seen:
            kept = seen[key]
            kept.evidence.references = list(dict.fromkeys([
                *kept.evidence.references, kept.evidence.source_ref,
                *item.evidence.references, item.evidence.source_ref]))
            continue
        copied = item.model_copy(deep=True)
        result.append(copied)
        if entity:
            seen[key] = copied
    return result, len(entities)


def visible_page(items: list[SearchItem], limit: int) -> ItemPage:
    unique, count = dedupe_items(items)
    visible = unique[:max(0, limit)]
    prices = [p for item in visible if (p := finite_price(item.price)) is not None]
    return ItemPage(items=visible, total=len(unique), raw_count=len(items), entity_count=count,
        cheapest=min(prices) if prices else None, dearest=max(prices) if prices else None)
