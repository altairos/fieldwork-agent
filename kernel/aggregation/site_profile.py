"""田野点画像聚合 — ``profile_field_site`` 能力。

用 ``concurrent.futures.ThreadPoolExecutor`` 并行调用 flyai 原子原语
（住宿/交通/文化/活动），组合为 ``FieldSiteProfile``。约定：

- 严格无状态：``client`` 经 keyword-only 参数注入，默认 None 时自建
  ``FlyAIClient()``；不读写任何项目状态；
- 逐原语失败隔离：单段失败（ok=False 或抛异常）置 None/[]，不影响整体
  ``ok=True``；全部源失败才返回 ``Envelope(ok=False, code="all_sources_failed")``；
- 住宿预算本地筛选为内核新增策略：条目价格不得超过上浮线
  ``budget_per_night * 1.5``、无价格条目原样保留（旧住宿段
  ``tools/flyai_tools.py`` ``find_accommodation_for_field_work`` 不做本地筛选、
  只把 ``max_price`` 交给远端；"解析失败即丢"是旧交通段口径，非住宿策略）；
- 每段截断 ``max_items_per_section``；``SummaryHints`` 从成功段统计。
"""

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from fieldwork_agent.kernel.models import (
    Envelope,
    FieldSiteProfile,
    ItemPage,
    SearchItem,
    SummaryHints,
    ToolError,
)
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from fieldwork_agent.kernel.sources.flyai import FlyAIClient
from pydantic import BaseModel

# 默认文化兴趣（search_pois 的 category 值），每项发起一次 POI 搜索
DEFAULT_INTERESTS: list[str] = ["人文古迹", "宗教场所", "古镇古村", "博物馆"]

# 住宿预算上浮系数（内核新增：本地按 budget*1.5 剔除超价条目）
BUDGET_MARKUP = 1.5


class ProfileFieldSiteArgs(BaseModel):
    """``profile_field_site`` 的入参模型（``client`` 注入参数不进 schema）。"""

    location: str
    budget_per_night: float = 200
    interests: list[str] | None = None
    departure_city: str = ""
    travel_date: str = ""
    max_items_per_section: int = 5


def _safe_call(fn: Callable, *args: Any, **kwargs: Any) -> Envelope:
    """执行单个源调用；异常/非包络返回都折成失败包络（失败隔离的最内层）。"""
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:  # 逐原语隔离：任何源的异常都不允许逃逸到聚合层
        return Envelope(ok=False, error=ToolError(
            code="source_exception",
            message=f"数据源调用异常：{exc}",
            hint="该段已置空，不影响其他段；可稍后重试",
        ))
    if isinstance(result, Envelope):
        return result
    return Envelope(ok=False, error=ToolError(
        code="source_bad_envelope",
        message=f"数据源未返回 Envelope：{type(result).__name__}",
        hint="该段已置空",
    ))


def _build_page(items: list[SearchItem], limit: int) -> ItemPage:
    """把筛选后的条目组成一页：截断到 ``limit``，total 记截断前条数与极值。"""
    prices = [i.price for i in items if i.price is not None]
    return ItemPage(
        items=items[:limit],
        total=len(items),
        cheapest=min(prices) if prices else None,
        dearest=max(prices) if prices else None,
    )


def _min_price(items: list[SearchItem]) -> float | None:
    """段内成功价最低值；无可用价格返回 None。"""
    prices = [i.price for i in items if i.price is not None]
    return min(prices) if prices else None


def profile_field_site(location: str, budget_per_night: float = 200,
                       interests: list[str] | None = None,
                       departure_city: str = "", travel_date: str = "",
                       max_items_per_section: int = 5, *,
                       client: FlyAIClient | None = None) -> Envelope:
    """聚合目的地田野点画像：住宿/交通/文化/活动四段，失败隔离。"""
    if client is None:
        client = FlyAIClient()
    if interests is None:
        interests = DEFAULT_INTERESTS

    # 每个任务 = (段名, 段内元信息, 无参调用)，并行提交到线程池
    tasks: list[tuple[str, str, Callable[[], Envelope]]] = [
        # 住宿三策略：民宿优先 + 酒店备选 + AI 语义（驻村意图，结果并入住宿段）
        ("accommodation", "", lambda: _safe_call(
            client.search_hotels, dest_name=location, check_in=travel_date,
            hotel_types="民宿,客栈", max_price=str(int(budget_per_night)))),
        ("accommodation", "", lambda: _safe_call(
            client.search_hotels, dest_name=location, check_in=travel_date,
            max_price=str(int(budget_per_night * BUDGET_MARKUP)))),
        ("accommodation", "", lambda: _safe_call(
            client.search_ai, f"{location} 适合长期驻村的便宜住宿 民宿 招待所")),
        # 当地文化活动：广义关键词覆盖
        ("amenities", "", lambda: _safe_call(
            client.search_keyword, f"{location} 文化活动 节庆 民俗")),
    ]
    # 文化兴趣：每个 interest 一次 POI 搜索（默认参数捕获避免闭包晚绑定）
    for interest in interests:
        tasks.append(("culture", interest, lambda i=interest: _safe_call(
            client.search_pois, location, category=i)))
    # 大交通：仅在给出出发城市时查询，航班 + 火车各一次
    if departure_city:
        tasks.append(("transport", "", lambda: _safe_call(
            client.search_flights, origin=departure_city, destination=location,
            dep_date=travel_date)))
        tasks.append(("transport", "", lambda: _safe_call(
            client.search_trains, origin=departure_city, destination=location,
            dep_date=travel_date)))

    # 并行执行；_safe_call 保证任务不抛异常，fut.result() 安全
    by_tag: dict[str, list[tuple[str, ItemPage]]] = defaultdict(list)
    succeeded = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fn) for _, _, fn in tasks]
        for (tag, meta, _), fut in zip(tasks, futures, strict=True):
            env = fut.result()
            if env.ok and isinstance(env.data, ItemPage):
                succeeded += 1
                by_tag[tag].append((meta, env.data))

    # 全部源失败（含仅 ok=False / 仅异常）→ 整体失败包络
    if succeeded == 0:
        return Envelope(ok=False, error=ToolError(
            code="all_sources_failed",
            message="田野点画像聚合失败：所有数据源均未返回有效结果",
            hint="flyai CLI 不可用或无网络，请确认 `flyai` 命令可用后重试",
        ))

    def merged_items(tag: str) -> list[SearchItem]:
        return [i for _, page in by_tag.get(tag, []) for i in page.items]

    # 住宿段：合并三策略（含 AI 归段）结果并按上浮线本地筛选；
    # price=None 原样保留，price>cap 剔除（350>300 剔除，180≤300 保留）。
    # 三策略全失败 → by_tag 缺 "accommodation" 键 → 该段为 None（非空页）。
    cap = budget_per_night * BUDGET_MARKUP
    acc_candidates = merged_items("accommodation")
    acc_filtered = [i for i in acc_candidates if i.price is None or i.price <= cap]
    accommodation = _build_page(acc_filtered, max_items_per_section) \
        if "accommodation" in by_tag else None

    transport_items = merged_items("transport")
    transport = _build_page(transport_items, max_items_per_section) \
        if "transport" in by_tag else None

    amenities_items = merged_items("amenities")
    amenities = _build_page(amenities_items, max_items_per_section) \
        if "amenities" in by_tag else None

    # 文化段：POI 条目转 dict 并标注所属兴趣，整体截断
    culture: list[dict[str, Any]] = []
    for meta, page in by_tag.get("culture", []):
        for item in page.items:
            entry = item.model_dump()
            entry["category"] = meta
            culture.append(entry)
    culture = culture[:max_items_per_section]

    final_items = {
        "accommodation": accommodation.items if accommodation else [],
        "transport": transport.items if transport else [],
        "amenities": amenities.items if amenities else [],
    }
    # R6：住宿段无任何可见供给（海外偏远点常态）→ 二段式建议，区分于"有供给但超预算"
    hints: dict[str, str] = {}
    if not final_items["accommodation"]:
        hints["accommodation"] = ("平台未见住宿供给：建议以最近枢纽城市住宿为基地，"
                                  "进村住宿通过当地学术合作方/接待方安排，需人工核实")
    summary = SummaryHints(
        section_counts={
            tag: len(items) for tag, items in final_items.items()
        } | {"culture": len(culture)},
        accommodation_nightly_min=_min_price(final_items["accommodation"]),
        transport_min=_min_price(final_items["transport"]),
        hints=hints,
    )

    profile = FieldSiteProfile(
        location=location,
        transport=transport,
        accommodation=accommodation,
        culture=culture,
        amenities=amenities,
        summary=summary,
        generated_at=datetime.now().isoformat(),
    )
    return Envelope(ok=True, data=profile)


register(CapabilityMeta(
    name="profile_field_site",
    description="聚合目的地田野点画像：住宿（内核新增本地预算上浮1.5筛选）/大交通/"
                "文化兴趣点/当地活动，并行调用数据源并逐段失败隔离；单段失败不影响整体；"
                "住宿零供给时 summary.hints 给出枢纽+学术接待二段式建议。",
    fn=profile_field_site,
    args_model=ProfileFieldSiteArgs,
))
