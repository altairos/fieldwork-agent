"""行程聚合 — ``compose_itinerary`` 能力（含日期修复）。

迁移自旧 ``tools/flyai_tools.py`` 的 ``comprehensive_travel_plan``：保留
"多目的地 + 分段住宿 + 调研节奏 + 预算"的组合意图，产出改为 ``ItineraryPlan`` 契约。
**日期铁律**：旧实现把退房日写成 ``f"2026-12-{min(31, 1 + days)}"`` 这类字符串拼接
（年份硬编码、跨月/跨年必错），本模块一律
``datetime.strptime(start_date, "%Y-%m-%d") + timedelta``，不拼任何月/日。

约定：

- 严格无状态：``client`` 经 keyword-only 参数注入，默认 None 时自建 ``FlyAIClient()``；
- 逐调用失败隔离：单腿无可用价格 → 该腿 ``chosen=None`` + ``note`` 说明、预算不计该条；
  单地住宿无价 → 该条晚价留空、预算不计该条；交通与住宿调用**全部**失败才返回
  ``Envelope(ok=False, code="all_sources_failed")``；任何源异常都不逃逸出模块；
- 备选：``plan.alternatives`` 由已抓到的未选交通候选汇成（零额外调用）；
- 日历口径：末日为退房与返程日，故 ``end_date = start + (duration_days - 1) 天``，
  住宿总夜数 = ``duration_days - 1``，按目的地均分（余数分给靠前的点）；
- 预算条目口径（审阅意见 R2，金额来源全部标注 ``source``）：交通 = 所选最低价 × 人数
  （逐腿计，多目的地不低估）；住宿 = 每晚最低价 × 该点夜数 × 房间数（默认 2 人/间
  假设，``rooms`` 可调）；``市内与餐饮`` = 人均日标准(``meals_local_per_person_day``，
  默认 150) × 人数 × 天数（estimate，不再按总预算百分比）。每条携带
  ``currency/unit/quantity/people/priced_at/source_ref`` 可复核口径；
- 快照回链（R08）：预算条目 ``source_ref`` 携带规格键（``@live:工具:参数``，由
  ``live_flight_ref`` 等纯函数生成）；宿主轮末以真实快照引用替换（web_host loop
  的 ``_resolve_live_refs``），kernel 不感知宿主；
- **预算完整性**：``coverage`` complete/partial/unknown 按腿与住宿报价可得性判定，
  缺失项进 ``uncovered``；``over_budget`` 仅在 complete 时为真——partial 时 total
  只是已知小计，不得据此宣布"预算内/超支"；
- 长住诚实：单点 > ``LONG_STAY_THRESHOLD`` 晚时，日价仅作估算线索，该 stay 标
  ``long_stay_unverified=True`` 并写入 degraded，不宣称长期住宿已落实；
- 末端接驳（PoC2 N2）：``amap_client`` 注入（None 自建），每腿"前一城市→到达点"
  公交方案进 ``leg["last_mile"]``（地名级 geocode 复用，每腿至多 2 geocode + 1
  transit）；首次 ``amap_invalid_key`` 即全程停用接驳（环境缺席不进 ``uncovered``、
  不降级核心 coverage）；查过无价与交通同口径进 ``uncovered``；预算联动
  ``cheapest×人数``（unit=高德公交最低价×人数）；``inputs_hash`` 不含接驳
  （增强非方案输入）。
"""

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from fieldwork_agent.kernel.aggregation.amap_refs import (
    live_geocode_ref,
    live_transit_ref,
)
from fieldwork_agent.kernel.models import (
    BudgetBreakdown,
    BudgetItem,
    Envelope,
    GeoLocation,
    ItemPage,
    ItineraryPlan,
    PhaseSegment,
    SearchItem,
    ToolError,
    compute_inputs_hash,
)
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from fieldwork_agent.kernel.sources.amap import AMapClient
from fieldwork_agent.kernel.sources.flyai import FlyAIClient
from pydantic import BaseModel

# 唯一的日期格式：所有日期都由 datetime + timedelta 产出，再用它格式化
DATE_FMT = "%Y-%m-%d"

# 调研节奏：适应 30% / 深入 50% / 补充 20%（取整为 0 天的段并入前段）
RHYTHM_RATIOS: list[tuple[str, float, str]] = [
    ("适应期", 0.30, "抵达安顿、建立关系、踩点与试探性短访谈"),
    ("深入期", 0.50, "密集访谈与参与观察，当日整理素材"),
    ("补充期", 0.20, "补访核验、材料归档与离场前回访"),
]

# 市内交通与餐饮估算的人均日标准（元/人·天，可由宿主调整；R2：不再按总预算百分比）
MEALS_PER_PERSON_DAY_DEFAULT = 150.0

# 单点住宿超过该夜数即视为长住：日价只能作估算线索（R2/R6）
LONG_STAY_THRESHOLD = 14

# 备选条目截断：腿内备选只保留有限条数，避免包络膨胀
MAX_ALTERNATIVES = 3


class ComposeItineraryArgs(BaseModel):
    """``compose_itinerary`` 的入参模型（``client``/``amap_client`` 注入参数不进 schema）。"""

    departure: str
    destinations: list[str]
    start_date: str
    duration_days: int
    budget_total: float
    researcher_count: int = 1
    rooms: int = 0  # 0 = 按 2 人/间假设自动推导
    meals_local_per_person_day: float = MEALS_PER_PERSON_DAY_DEFAULT


# ---------------------------------------------------------------- 内部工具


def _safe_call(fn: Callable, *args: Any, **kwargs: Any) -> Envelope:
    """执行单个源调用；异常/非包络返回都折成失败包络（失败隔离的最内层）。"""
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:  # 逐源隔离：任何源的异常都不允许逃逸到聚合层
        return Envelope(ok=False, error=ToolError(
            code="source_exception",
            message=f"数据源调用异常：{exc}",
            hint="该腿/该段已按无价处理，不影响其他段；可稍后重试",
        ))
    if isinstance(result, Envelope):
        return result
    return Envelope(ok=False, error=ToolError(
        code="source_bad_envelope",
        message=f"数据源未返回 Envelope：{type(result).__name__}",
        hint="该腿/该段已按无价处理",
    ))


def _fmt(dt: datetime) -> str:
    return dt.strftime(DATE_FMT)


def _error(code: str, message: str, hint: str) -> Envelope:
    return Envelope(ok=False, error=ToolError(code=code, message=message, hint=hint))


def _validate(departure: str, destinations: list[str], duration_days: int,
              budget_total: float, researcher_count: int) -> Envelope | None:
    """入参校验：命中即返回 bad_input 错误包络，否则 None。"""
    problems: list[str] = []
    if not departure or not departure.strip():
        problems.append("departure 不能为空")
    if not destinations:
        problems.append("destinations 不能为空")
    if duration_days < 1:
        problems.append("duration_days 必须 ≥ 1")
    if budget_total <= 0:
        problems.append("budget_total 必须 > 0")
    if researcher_count < 1:
        problems.append("researcher_count 必须 ≥ 1")
    if not problems:
        return None
    return _error("bad_input", "；".join(problems),
                  "请给出出发地、至少一个目的地、YYYY-MM-DD 起始日期、正整数天数与正数预算")


def _split_nights(total_nights: int, count: int) -> list[int]:
    """把住宿总夜数均分到各目的地，余数分给靠前的点。"""
    base, extra = divmod(total_nights, count)
    return [base + (1 if i < extra else 0) for i in range(count)]


def _build_rhythm(duration_days: int) -> list[PhaseSegment]:
    """30/50/20 节奏分段；取整为 0 天的段并入前段（不新增空段）。"""
    segments: list[PhaseSegment] = []
    for label, ratio, note in RHYTHM_RATIOS:
        days = int(duration_days * ratio)
        if days <= 0:
            continue
        segments.append(PhaseSegment(label=label, days=days, note=note))
    if not segments and duration_days >= 1:
        # 极短行程（1 天）三段全为 0：兜底给唯一可排的深入期
        segments.append(PhaseSegment(label="深入期", days=duration_days,
                                     note=RHYTHM_RATIOS[1][2]))
    return segments


def _page_items(env: Envelope | None) -> list[SearchItem]:
    """安全取条目：None / 失败包络 / 非 ItemPage 都视为空。"""
    if env is not None and env.ok and isinstance(env.data, ItemPage):
        return list(env.data.items)
    return []


def _candidates(items: list[SearchItem], mode: str, guesthouse: bool = False) -> list[dict]:
    """条目转候选 dict：保留名称/价格/方式标签，并透传 ``price_raw`` 与 ``extra``
    （R2：原始报价与时长/换乘等来源信息不得在候选阶段丢失）；同时透传
    ``jump_url``/``pic_url``（A3：预订链接与图片随 chosen/alternatives 进交付物）。"""
    return [{"name": i.name, "price": i.price, "mode": mode, "guesthouse": guesthouse,
             "price_raw": i.price_raw, "extra": i.extra,
             "jump_url": i.jump_url, "pic_url": i.pic_url}
            for i in items]


def _select(cands: list[dict], prefer_key: str | None = None
            ) -> tuple[dict | None, list[dict]]:
    """最低价优先；``prefer_key`` 仅用于同价定序（同价优先民宿）。无可用价时 chosen=None。"""
    priced = sorted(
        [c for c in cands if c["price"] is not None],
        key=lambda c: (c["price"], 0 if prefer_key and c.get(prefer_key) else 1),
    )
    unpriced = [c for c in cands if c["price"] is None]
    chosen = priced[0] if priced else None
    return chosen, (priced[1:] + unpriced)[:MAX_ALTERNATIVES]


def live_flight_ref(origin: str, dest: str, dep_date: str) -> str:
    """规格键（R08 §6）：只依赖入参，宿主轮末据此替换为真实快照引用。"""
    return f"@live:search_flights:{origin}>{dest}|{dep_date}"


def live_train_ref(origin: str, dest: str, dep_date: str) -> str:
    return f"@live:search_trains:{origin}>{dest}|{dep_date}"


def live_hotel_ref(dest: str, check_in: str, check_out: str,
                   hotel_types: str = "") -> str:
    ref = f"@live:search_hotels:{dest}|{check_in}|{check_out}"
    return f"{ref}|{hotel_types}" if hotel_types else ref


# ---------------------------------------------------------------- 末端接驳（N2）


def _memo_geocode(amap_client: AMapClient, address: str,
                  memo: dict[str, GeoLocation]) -> tuple[GeoLocation | None, ToolError | None]:
    """地名级 geocode 复用：同地名跨腿只解析一次；失败不缓存（下次仍重试）。"""
    if address in memo:
        return memo[address], None
    env = _safe_call(amap_client.geocode, address, city=address)
    if env.ok and isinstance(env.data, GeoLocation):
        memo[address] = env.data
        return env.data, None
    return None, env.error


def _plan_leg_last_mile(amap_client: AMapClient, hop: dict[str, str],
                        memo: dict[str, GeoLocation]) -> tuple[dict[str, Any], bool, bool, bool]:
    """单腿末端接驳（PoC2 §3.2）：前一城市→到达点的公交方案。

    返回 ``(last_mile, queried, has_price, invalid_key)``；``invalid_key`` 由调用方
    据此全程停用后续接驳查询（无 Key 属环境缺席而非数据缺口，不进 uncovered）。
    N5 §4.3 C3：``live_refs`` 只收集实际成功的高德调用规格键——geocode 成功
    （含 memo 命中，轮内该地名必曾真实调用过）恒附，transit 键在调用 ok
    时附（有无报价快照均真实，宿主登记所有 ok 调用）。
    """
    refs: list[str] = []
    hub_geo, hub_err = _memo_geocode(amap_client, hop["from"], memo)
    if hub_err is not None and hub_err.code == "amap_invalid_key":
        return {"hub_geo": None, "site_geo": None, "options": None,
                "note": "未配置高德 API Key，接驳查询已停用",
                "live_refs": refs}, True, False, True
    if hub_geo is None:
        return {"hub_geo": None, "site_geo": None, "options": None,
                "note": f"接驳未纳入：{hop['from']} 地名解析失败（{hub_err.code}），"
                        "预算未计该条",
                "live_refs": refs}, True, False, False
    refs.append(live_geocode_ref(hop["from"]))
    site_geo, site_err = _memo_geocode(amap_client, hop["to"], memo)
    if site_err is not None and site_err.code == "amap_invalid_key":
        return {"hub_geo": None, "site_geo": None, "options": None,
                "note": "未配置高德 API Key，接驳查询已停用",
                "live_refs": refs}, True, False, True
    if site_geo is None:
        return {"hub_geo": hub_geo, "site_geo": None, "options": None,
                "note": f"接驳未纳入：{hop['to']} 地名解析失败（{site_err.code}），"
                        "预算未计该条",
                "live_refs": refs}, True, False, False
    refs.append(live_geocode_ref(hop["to"]))
    transit_env = _safe_call(
        amap_client.direction_transit, origin=hub_geo.location,
        destination=site_geo.location, city=hub_geo.city, cityd=site_geo.city)
    page = transit_env.data if transit_env.ok and isinstance(transit_env.data, ItemPage) else None
    if page is not None:
        refs.append(live_transit_ref(hub_geo.location, site_geo.location))
    if page is None:
        detail = transit_env.error.message if transit_env.error else "无公交方案"
        return {"hub_geo": hub_geo, "site_geo": site_geo, "options": None,
                "note": f"接驳公交查询失败：{detail}，预算未计该条",
                "live_refs": refs}, True, False, False
    if page.cheapest is None or page.cheapest <= 0:
        return {"hub_geo": hub_geo, "site_geo": site_geo, "options": None,
                "note": "接驳公交无可用报价，预算未计该条",
                "live_refs": refs}, True, False, False
    return {"hub_geo": hub_geo, "site_geo": site_geo, "options": page,
            "note": "", "live_refs": refs}, True, True, False


# ---------------------------------------------------------------- 能力实现


def compose_itinerary(departure: str, destinations: list[str], start_date: str,
                      duration_days: int, budget_total: float, researcher_count: int = 1,
                      rooms: int = 0, meals_local_per_person_day: float = MEALS_PER_PERSON_DAY_DEFAULT,
                      *, client: FlyAIClient | None = None,
                      amap_client: AMapClient | None = None) -> Envelope:
    """组合多目的地调研行程：交通对比、滚动住宿、节奏缓冲与可复核预算（日期只用 timedelta）。"""
    if client is None:
        client = FlyAIClient()
    if amap_client is None:
        amap_client = AMapClient()  # 与 client 对称自建；无 Key 时首调即短路停用接驳
    bad = _validate(departure, destinations, duration_days, budget_total, researcher_count)
    if bad is not None:
        return bad
    try:
        start_dt = datetime.strptime(start_date, DATE_FMT)
    except ValueError:
        return _error("bad_start_date", f"start_date 不是 YYYY-MM-DD 格式：{start_date!r}",
                      "改用 ISO 日期（如 2026-11-01）；日期由内核按 timedelta 滚动")

    # 1) 先把日历算完（纯算术，无 I/O）：末日退房返程，故总夜数 = 天数 - 1
    end_dt = start_dt + timedelta(days=duration_days - 1)
    nights = _split_nights(duration_days - 1, len(destinations))
    windows: list[dict[str, Any]] = []
    cursor = start_dt
    for dest, n in zip(destinations, nights, strict=True):
        check_out_dt = cursor + timedelta(days=n)
        windows.append({"dest": dest, "nights": n,
                        "check_in": _fmt(cursor), "check_out": _fmt(check_out_dt)})
        cursor = check_out_dt
    # 腿：出发地→首站、站间滚动、末站→出发地（返程日为 end_date）
    hops: list[dict[str, str]] = []
    prev = departure
    for i, w in enumerate(windows):
        hops.append({"from": prev, "to": w["dest"], "date": w["check_in"],
                     "direction": "outbound" if i == 0 else "inter"})
        prev = w["dest"]
    hops.append({"from": prev, "to": departure, "date": _fmt(end_dt), "direction": "return"})

    # 2) 组装叶子调用（交通对比 / 住宿两策略），并行执行
    tasks: list[tuple[str, Any, Callable[[], Envelope]]] = []
    for idx, hop in enumerate(hops):
        tasks.append(("transport", (idx, "flight"), lambda h=hop: _safe_call(
            client.search_flights, origin=h["from"], destination=h["to"], dep_date=h["date"])))
        tasks.append(("transport", (idx, "train"), lambda h=hop: _safe_call(
            client.search_trains, origin=h["from"], destination=h["to"], dep_date=h["date"])))
    for idx, w in enumerate(windows):
        if w["nights"] <= 0:
            continue
        tasks.append(("hotel", (idx, "民宿"), lambda w=w: _safe_call(
            client.search_hotels, dest_name=w["dest"], check_in=w["check_in"],
            check_out=w["check_out"], hotel_types="民宿,客栈")))
        tasks.append(("hotel", (idx, "酒店"), lambda w=w: _safe_call(
            client.search_hotels, dest_name=w["dest"], check_in=w["check_in"],
            check_out=w["check_out"])))

    transport_cands: dict[int, list[dict]] = defaultdict(list)
    hotel_cands: dict[int, list[dict]] = defaultdict(list)
    core_ok = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fn) for _, _, fn in tasks]
        for (tag, meta, _), fut in zip(tasks, futures, strict=True):
            env = fut.result()
            if env.ok:
                core_ok += 1
            if tag == "transport":
                idx, mode = meta
                transport_cands[idx].extend(_candidates(_page_items(env), mode))
            else:
                idx, kind = meta
                hotel_cands[idx].extend(
                    _candidates(_page_items(env), kind, guesthouse=kind == "民宿"))

    # 3) 交通与住宿一条都没成功 → 整体失败（与 profile_field_site 同口径）
    if core_ok == 0:
        return _error("all_sources_failed",
                      "行程聚合失败：所有交通与住宿数据源均未返回有效结果",
                      "flyai CLI 不可用或无网络，请确认 `flyai` 命令可用后重试")

    # 4) 腿与住宿装配：无价即降级，不抛异常；末端接驳（N2）随腿计算
    notes: list[str] = []
    uncovered: list[str] = []
    legs: list[dict[str, Any]] = []
    lm_priced: list[bool] = []
    lm_queried: list[bool] = []
    geo_memo: dict[str, GeoLocation] = {}  # 地名级 geocode 复用：同地名跨腿零重复
    amap_disabled = False  # 无 Key 首败即全程停用（增强缺席，不惩罚核心口径）
    for idx, hop in enumerate(hops):
        chosen, alts = _select(transport_cands.get(idx, []))
        if chosen is None:
            notes.append(f"交通（{hop['from']}→{hop['to']}）无可用价格，预算未计该条")
        if amap_disabled:
            last_mile = {"hub_geo": None, "site_geo": None, "options": None,
                         "note": "未配置高德 API Key，接驳查询已停用",
                         "live_refs": []}
            leg_queried = leg_priced = leg_no_key = False
        else:
            last_mile, leg_queried, leg_priced, leg_no_key = \
                _plan_leg_last_mile(amap_client, hop, geo_memo)
            if leg_no_key:
                amap_disabled = True
        # 查过但无价与交通同口径进 uncovered；无 Key 停用属环境缺席，不计缺口
        if leg_queried and not leg_priced and not leg_no_key:
            uncovered.append(f"接驳（{hop['from']}→{hop['to']}）")
        lm_queried.append(leg_queried)
        lm_priced.append(leg_priced)
        legs.append({**hop, "chosen": chosen, "alternatives": alts,
                     "note": "" if chosen else "无可用价格：数据源失败或结果未含报价，"
                                               "预算未计该条，请人工核验",
                     "last_mile": last_mile})

    stays: list[dict[str, Any]] = []
    for idx, w in enumerate(windows):
        if w["nights"] <= 0:
            notes.append(f"{w['dest']} 未排到住宿夜：天数不足以覆盖全部目的地")
            continue
        chosen, alts = _select(hotel_cands.get(idx, []), prefer_key="guesthouse")
        if chosen is None:
            notes.append(f"{w['dest']} 住宿无可用价格，预算未计该条")
        stay: dict[str, Any] = {"dest": w["dest"], "check_in": w["check_in"],
                                "check_out": w["check_out"], "nights": w["nights"],
                                "nightly_price": chosen["price"] if chosen else None,
                                "chosen": chosen, "alternatives": alts}
        # R2/R6：长住只拿到日价→标估算待核验，不宣称长期住宿已落实
        if w["nights"] > LONG_STAY_THRESHOLD:
            stay["long_stay_unverified"] = True
            notes.append(f"{w['dest']} 长住 {w['nights']} 晚：日价为短住报价估算，"
                         "未确认长期可订与月租口径，待人工核验")
        stays.append(stay)

    # 5) 预算（R2）：可算条目才计入；房间数默认 2 人/间；估算条目口径可解释
    rooms_eff = rooms if rooms > 0 else max(1, (researcher_count + 1) // 2)
    priced_at = datetime.now().isoformat()
    items: list[BudgetItem] = []
    priced_count = 0
    for leg in legs:
        if leg["chosen"] is not None:
            priced_count += 1
            ref_fn = (live_train_ref if leg["chosen"].get("mode") == "train"
                      else live_flight_ref)
            items.append(BudgetItem(name=f"交通（{leg['from']}→{leg['to']}）",
                                    amount=round(leg["chosen"]["price"] * researcher_count, 2),
                                    source="cheapest", unit="票价×人数",
                                    quantity=1.0, people=researcher_count,
                                    priced_at=priced_at,
                                    source_ref=f"{leg['chosen']['name']}"
                                               f"（{ref_fn(leg['from'], leg['to'], leg['date'])}）"))
        else:
            uncovered.append(f"交通（{leg['from']}→{leg['to']}）")
    # 末端接驳预算（N2）：cheapest×人数，口径标注高德来源；无价已在腿装配进 uncovered
    for leg, leg_priced in zip(legs, lm_priced, strict=True):
        if not leg_priced:
            continue
        priced_count += 1
        lm = leg["last_mile"]
        first = next((i for i in lm["options"].items if i.price is not None), None)
        items.append(BudgetItem(
            name=f"接驳（{leg['from']}→{leg['to']}）",
            amount=round(lm["options"].cheapest * researcher_count, 2),
            source="cheapest", unit="高德公交最低价×人数",
            quantity=1.0, people=researcher_count, priced_at=priced_at,
            source_ref=f"{first.name}（高德公交）" if first else "高德公交"))
    for stay in stays:
        if stay["nightly_price"] is not None:
            priced_count += 1
            hotel_ref = (live_hotel_ref(stay["dest"], stay["check_in"], stay["check_out"],
                                        "民宿,客栈" if stay["chosen"].get("guesthouse") else "")
                         if stay["chosen"] else "")
            items.append(BudgetItem(name=f"住宿（{stay['dest']}）",
                                    amount=round(stay["nightly_price"] * stay["nights"]
                                                 * rooms_eff, 2),
                                    source="cheapest", unit="每晚价×夜数×房间数",
                                    quantity=float(stay["nights"]), people=rooms_eff,
                                    priced_at=priced_at,
                                    source_ref=f"{stay['chosen']['name']}（{hotel_ref}）"
                                    if stay["chosen"] else ""))
        else:
            uncovered.append(f"住宿（{stay['dest']}）")
    meal_amount = round(meals_local_per_person_day * researcher_count * duration_days, 2)
    items.append(BudgetItem(name="市内与餐饮", amount=meal_amount, source="estimate",
                            unit="人均每日×人数×天数", quantity=float(duration_days),
                            people=researcher_count))
    total = round(sum(i.amount for i in items), 2)
    # coverage：全部预期报价可得=complete；部分可得=partial；一条都没有=unknown
    if not uncovered:
        coverage = "complete"
    elif priced_count > 0:
        coverage = "partial"
    else:
        coverage = "unknown"
    budget = BudgetBreakdown(items=items, total=total, budget_total=budget_total,
                             over_budget=coverage == "complete" and total > budget_total,
                             coverage=coverage, uncovered=uncovered)

    # 6) 备选：由已抓到的未选交通候选汇成（零额外调用）
    alternatives: list[dict[str, Any]] = [
        {"from": leg["from"], "to": leg["to"], "items": leg["alternatives"]}
        for leg in legs if leg["alternatives"]
    ]

    plan = ItineraryPlan(
        departure=departure,
        destinations=list(destinations),
        start_date=_fmt(start_dt),
        end_date=_fmt(end_dt),
        legs=legs,
        stays=stays,
        rhythm=_build_rhythm(duration_days),
        buffer_days=max(1, duration_days // 10),
        alternatives=alternatives,
        budget=budget,
        generated_at=datetime.now().isoformat(),
        inputs_hash=compute_inputs_hash({
            "departure": departure, "destinations": list(destinations),
            "start_date": start_date, "duration_days": duration_days,
            "budget_total": budget_total, "researcher_count": researcher_count,
            "rooms": rooms_eff}),
    )
    return Envelope(ok=True, data=plan, degraded="；".join(notes) if notes else None)


register(CapabilityMeta(
    name="compose_itinerary",
    description="组合多目的地调研行程：去回程交通取最低价并留备选、住宿按天数滚动分段"
                "（日期一律 strptime+timedelta）、30/50/20 调研节奏与缓冲日、"
                "预算条目带币种/单位/数量/报价时间/来源引用，coverage 标记完整性"
                "（仅 complete 才允许超支结论）、长住日价标待核验；单腿无价只降级不报错。",
    fn=compose_itinerary,
    args_model=ComposeItineraryArgs,
))
