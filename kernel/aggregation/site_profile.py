"""田野点画像聚合 — ``profile_field_site`` 能力。

用 ``concurrent.futures.ThreadPoolExecutor`` 并行调用 flyai 原子原语
（住宿/交通/文化/活动），叠加高德地理底座（geo）与周边设施盘点
（facilities）两段，组合为 ``FieldSiteProfile``。约定：

- 严格无状态：``client`` / ``amap_client`` 经 keyword-only 参数注入，
  默认 None 时各自自建；不读写任何项目状态；
- 逐原语失败隔离：单段失败（ok=False 或抛异常）置 None/[]，不影响整体
  ``ok=True``；flyai 各段与高德 geocode 全部失败才返回
  ``Envelope(ok=False, code="all_sources_failed")``；geocode 失败（含海外
  地名 ``amap_no_result``）时 geo/facilities 缺省并在 ``degraded`` 附
  双源回退说明——画像回退纯 flyai 链路；
- 高德调用预算：单次画像 ≤6 次请求（1 geocode + 5 around，PoC §5.1）；
- 住宿预算本地筛选为内核新增策略：条目价格不得超过上浮线
  ``budget_per_night * 1.5``、无价格条目原样保留（旧住宿段
  ``tools/flyai_tools.py`` ``find_accommodation_for_field_work`` 不做本地筛选、
  只把 ``max_price`` 交给远端；"解析失败即丢"是旧交通段口径，非住宿策略）；
- 每段截断 ``max_items_per_section``；``SummaryHints`` 从成功段统计。
"""

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime
from typing import Any

from fieldwork_agent.kernel.aggregation.amap_refs import (
    live_around_ref,
    live_geocode_ref,
)
from fieldwork_agent.kernel.aggregation.lodging import LodgingComparisonRequest, compare_lodging
from fieldwork_agent.kernel.aggregation.spatial import SpatialQuery, execute_matrix
from fieldwork_agent.kernel.models import (
    Envelope,
    FieldSiteProfile,
    GeoLocation,
    ItemPage,
    SearchItem,
    SummaryHints,
    ToolError,
)
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from fieldwork_agent.kernel.source_requests import source_task
from fieldwork_agent.kernel.sources.amap import AMapClient
from fieldwork_agent.kernel.sources.flyai import FlyAIClient
from fieldwork_agent.kernel.travel import visible_page
from pydantic import BaseModel, Field

# 默认文化兴趣（search_pois 的 category 值），每项发起一次 POI 搜索
DEFAULT_INTERESTS: list[str] = ["人文古迹", "宗教场所", "古镇古村", "博物馆"]

# 周边设施盘点关键词（around_search 的 keywords 值），每项发起一次周边搜索；
# 医疗/警务/金融/物流/集市五类驻村保障（PoC §5.1，与预算 5 次 around 对应）
DEFAULT_FACILITY_KEYWORDS: list[str] = ["卫生院 诊所", "派出所", "银行", "快递", "集贸市场"]

# 周边搜索半径（米）：村级田野点的设施覆盖圈
FACILITY_RADIUS = 5000

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
    comparison: LodgingComparisonRequest | None = None
    query_matrix: list[SpatialQuery] | None = Field(default=None, min_length=1, max_length=4)
    facility_keywords: list[str] | None = Field(default=None, max_length=5)
    facility_radius: int = Field(default=5000, ge=100, le=50000)
    include_accommodation: bool = Field(default=True, description="本轮只找观察场所、不查住宿时设false")


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
    return visible_page(items, limit)


def _min_price(items: list[SearchItem]) -> float | None:
    """段内成功价最低值；无可用价格返回 None。"""
    prices = [i.price for i in items if i.price is not None]
    return min(prices) if prices else None


@source_task
def profile_field_site(location: str, budget_per_night: float = 200,
                       interests: list[str] | None = None,
                       departure_city: str = "", travel_date: str = "",
                       max_items_per_section: int = 5,
                       comparison: dict | LodgingComparisonRequest | None = None,
                       query_matrix: list[dict | SpatialQuery] | None = None,
                       facility_keywords: list[str] | None = None,
                       facility_radius: int = 5000, include_accommodation: bool = True, *,
                       client: FlyAIClient | None = None,
                       amap_client: AMapClient | None = None) -> Envelope:
    """聚合目的地田野点画像：住宿/交通/文化/活动四段 + 高德 geo/facilities 段。

    高德注入参数名 ``amap_client``（非设计稿字面 geo_client）：与三宿主统一
    高德注入键对齐（MCP RESERVED_KEYS / orchestrator inject 标记）。
    """
    if client is None:
        client = FlyAIClient()
    if amap_client is None:
        amap_client = AMapClient()
    if interests is None:
        interests = DEFAULT_INTERESTS
    comparison_request = (comparison if isinstance(comparison, LodgingComparisonRequest)
                          else LodgingComparisonRequest.model_validate(comparison) if comparison else None)
    if query_matrix is not None and not 1 <= len(query_matrix) <= 4:
        return Envelope(ok=False, error=ToolError(code="invalid_arguments", message="空间矩阵须为1至4路"))
    spatial_rows = [r if isinstance(r, SpatialQuery) else SpatialQuery.model_validate(r) for r in query_matrix or []]
    if spatial_rows:
        interests = []  # 用户任务已有研究场景路线，无需固定的旅游文化类别

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
    if spatial_rows:
        tasks = tasks[:2]  # 先查两种结构化住宿；有足够候选时不再追加泛词/语义商品检索
    if not include_accommodation:
        tasks = [task for task in tasks if task[0] != "accommodation"]
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

    if include_accommodation and comparison_request and comparison_request.hub_location:
        tasks.append(("accommodation", "hub", lambda: _safe_call(client.search_hotels,
            dest_name=comparison_request.hub_location, check_in=travel_date,
            max_price=str(int(budget_per_night * BUDGET_MARKUP)))))

    # 并行执行；_safe_call 保证任务不抛异常，fut.result() 安全
    by_tag: dict[str, list[tuple[str, ItemPage]]] = defaultdict(list)
    succeeded = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        # N05：copy_context().run 使工作线程继承提交时 contextvar（当前调用实例）
        futures = [pool.submit(copy_context().run, fn) for _, _, fn in tasks]
        for (tag, meta, _), fut in zip(tasks, futures, strict=True):
            env = fut.result()
            if env.ok and isinstance(env.data, ItemPage):
                if tag == "accommodation":
                    for item in env.data.items:
                        item.extra = {**item.extra, "base_kind": "hub" if meta == "hub" else "field",
                            "base_city": comparison_request.hub_location if meta == "hub" else location}
                succeeded += 1
                by_tag[tag].append((meta, env.data))

    # ── 高德 geo/facilities 段（PoC §5.1；预算 1 geocode + 5 around ≤6 次）──
    # geocode 先行（around 需要坐标锚点）；失败（含海外 amap_no_result）→
    # geo/facilities=None 且不发 around，degraded 附说明，不拖累整体 ok。
    geo: GeoLocation | None = None
    facilities: ItemPage | None = None
    degraded: str | None = None
    live_refs: list[str] = []  # N5 §4.3 C3：实际成功的高德调用规格键，宿主轮末替换
    geo_env = _safe_call(amap_client.geocode, address=location)
    if geo_env.ok and isinstance(geo_env.data, GeoLocation):
        geo = geo_env.data
        succeeded += 1  # 高德也算源：flyai 全挂但 geocode 成功仍可出 geo-only 画像
        live_refs.append(live_geocode_ref(location))
        if geo.ambiguous:
            degraded = "地名有多个匹配，周边检索需先核实锚点"
        if geo.location and not geo.ambiguous:
            facility_terms = DEFAULT_FACILITY_KEYWORDS if facility_keywords is None else facility_keywords
            geo_tasks = [lambda kw=kw: _safe_call(
                amap_client.around_search, keywords=kw,
                location=geo.location, radius=facility_radius)
                for kw in facility_terms]
            with ThreadPoolExecutor(max_workers=5) as pool:
                fac_envs = [fut.result() for fut in
                            [pool.submit(copy_context().run, t) for t in geo_tasks]]
            fac_items: list[SearchItem] = []
            any_around_ok = False
            for fac_env, fac_kw in zip(fac_envs, facility_terms, strict=True):
                if fac_env.ok and isinstance(fac_env.data, ItemPage):
                    any_around_ok = True
                    live_refs.append(live_around_ref(fac_kw, geo.location, facility_radius))
                    for item in fac_env.data.items:  # 跨关键词同名去重，首见保留
                        if item.name:
                            # N6 地图面板：附命中关键词（五类色分数据源，前端不猜
                            # 语义）；extra.location 的 POI 坐标随条目透传。
                            item.extra = {**item.extra, "facility_kind": fac_kw}
                            fac_items.append(item)
            if any_around_ok:  # 全部 around 失败 → 缺段 None（区分于空页）
                facilities = _build_page(fac_items, max_items_per_section)
    else:
        # 无 Key 单独点名（默认无 Key 部署的第一路径，指对排查方向：配 Key，
        # 而非误导为地名未收录/服务不可用）；境外 amap_no_result 明示功能缺口
        # （正在开发中，不静默跳过）；其余失败维持双源回退口径。
        err_code = geo_env.error.code if geo_env.error is not None else ""
        if err_code == "amap_invalid_key":
            degraded = "地理底座：未配置高德 API Key，已回退纯 FlyAI 文本链路"
        elif err_code == "amap_no_result":
            degraded = ("地理底座：高德未收录（境外田野点的定位/周边设施"
                        "正在开发中，后续版本将追加），已回退纯 FlyAI 文本链路")
        else:
            degraded = f"地理底座不可用（{err_code}）：{geo_env.error.hint if geo_env.error else '请检查数据源返回'}；地理事实仍待核实"

    # 全部源失败（含仅 ok=False / 仅异常；geocode 亦无结果）→ 整体失败包络
    spatial_results = execute_matrix(spatial_rows, geo, location, amap_client) if spatial_rows else []
    if any(any(q["ok"] for q in r["requests"]) for r in spatial_results):
        succeeded += 1
    if succeeded == 0:
        return Envelope(ok=False, error=ToolError(
            code="all_sources_failed",
            message="田野点画像聚合失败：所有数据源均未返回有效结果",
            hint=geo_env.error.hint if geo_env.error else "请检查旅行与地理来源配置；保留缺口，不反复更换检索词",
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

    # R1：AI 语义搜索只回 answer 无 items——收进 hints，不在 items 合并中静默丢失
    acc_answers = [page.answer for _, page in by_tag.get("accommodation", [])
                   if getattr(page, "answer", None) and page.answer.strip()]

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
    if include_accommodation and not final_items["accommodation"]:
        hints["accommodation"] = ("平台未见住宿供给：建议以最近枢纽城市住宿为基地，"
                                  "进村住宿通过当地学术合作方/接待方安排，需人工核实")
    if acc_answers:
        hints["accommodation_ai"] = acc_answers[0]
    summary = SummaryHints(
        section_counts={
            tag: len(items) for tag, items in final_items.items()
        } | {"culture": len(culture),
             "geo": 1 if geo is not None else 0,
             "facilities": len(facilities.items) if facilities else 0},
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
        geo=geo,
        facilities=facilities,
        live_refs=live_refs,
        summary=summary,
        generated_at=datetime.now().isoformat(),
        spatial_results=spatial_results,
        lodging_comparison=compare_lodging(visible_page(acc_filtered, len(acc_filtered)).items, geo, comparison_request, travel_date, amap_client)
            if comparison_request and geo and accommodation else [],
    )
    return Envelope(ok=True, data=profile, degraded=degraded)


register(CapabilityMeta(
    name="profile_field_site",
    description="聚合目的地田野点画像：住宿（内核新增本地预算上浮1.5筛选）/大交通/"
                "文化兴趣点/当地活动，并行调用数据源并逐段失败隔离；单段失败不影响整体；"
                "住宿零供给时 summary.hints 给出枢纽+学术接待二段式建议；"
                "高德可用时叠加地理底座（五级行政区划+坐标）与周边设施盘点"
                "（医疗/警务/金融/物流/集市，半径 5km），海外地名自动回退纯飞猪链路。",
    fn=profile_field_site,
    args_model=ProfileFieldSiteArgs,
))
