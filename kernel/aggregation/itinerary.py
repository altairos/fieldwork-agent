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
- 预算条目口径（金额来源全部标注 ``source``）：交通 = 所选最低价 × 人数 × 腿数
  （去程腿 + 返程腿即 brief 的 ×2，多目的地时逐腿取实价合计，避免低估）；
  住宿 = 每晚最低价 × 该点夜数；``市内与餐饮`` = ``0.15 × budget_total``（estimate）。
"""

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from fieldwork_agent.kernel.models import (
    BudgetBreakdown,
    BudgetItem,
    Envelope,
    ItemPage,
    ItineraryPlan,
    PhaseSegment,
    SearchItem,
    ToolError,
)
from fieldwork_agent.kernel.registry import CapabilityMeta, register
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

# 市内交通与餐饮的估算系数（按总预算比例，source="estimate" 便于复核）
CITY_MEAL_RATIO = 0.15

# 备选条目截断：腿内备选只保留有限条数，避免包络膨胀
MAX_ALTERNATIVES = 3


class ComposeItineraryArgs(BaseModel):
    """``compose_itinerary`` 的入参模型（``client`` 注入参数不进 schema）。"""

    departure: str
    destinations: list[str]
    start_date: str
    duration_days: int
    budget_total: float
    researcher_count: int = 1


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
    """条目转备选候选 dict：保留名称、价格与方式标签（price=None 也保留以便追溯）。"""
    return [{"name": i.name, "price": i.price, "mode": mode, "guesthouse": guesthouse}
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


# ---------------------------------------------------------------- 能力实现


def compose_itinerary(departure: str, destinations: list[str], start_date: str,
                      duration_days: int, budget_total: float, researcher_count: int = 1, *,
                      client: FlyAIClient | None = None) -> Envelope:
    """组合多目的地调研行程：交通对比、滚动住宿、节奏缓冲与预算（日期只用 timedelta）。"""
    if client is None:
        client = FlyAIClient()
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
            client.search_hotels, w["dest"], check_in=w["check_in"],
            check_out=w["check_out"], hotel_types="民宿,客栈")))
        tasks.append(("hotel", (idx, "酒店"), lambda w=w: _safe_call(
            client.search_hotels, w["dest"], check_in=w["check_in"],
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

    # 4) 腿与住宿装配：无价即降级，不抛异常
    notes: list[str] = []
    legs: list[dict[str, Any]] = []
    for idx, hop in enumerate(hops):
        chosen, alts = _select(transport_cands.get(idx, []))
        if chosen is None:
            notes.append(f"交通（{hop['from']}→{hop['to']}）无可用价格，预算未计该条")
        legs.append({**hop, "chosen": chosen, "alternatives": alts,
                     "note": "" if chosen else "无可用价格：数据源失败或结果未含报价，"
                                               "预算未计该条，请人工核验"})

    stays: list[dict[str, Any]] = []
    for idx, w in enumerate(windows):
        if w["nights"] <= 0:
            notes.append(f"{w['dest']} 未排到住宿夜：天数不足以覆盖全部目的地")
            continue
        chosen, alts = _select(hotel_cands.get(idx, []), prefer_key="guesthouse")
        if chosen is None:
            notes.append(f"{w['dest']} 住宿无可用价格，预算未计该条")
        stays.append({"dest": w["dest"], "check_in": w["check_in"], "check_out": w["check_out"],
                      "nights": w["nights"],
                      "nightly_price": chosen["price"] if chosen else None,
                      "chosen": chosen, "alternatives": alts})

    # 5) 预算：可算条目才计入；估算条目恒在（便于复核口径）
    items: list[BudgetItem] = []
    for leg in legs:
        if leg["chosen"] is not None:
            items.append(BudgetItem(name=f"交通（{leg['from']}→{leg['to']}）",
                                    amount=round(leg["chosen"]["price"] * researcher_count, 2),
                                    source="cheapest"))
    for stay in stays:
        if stay["nightly_price"] is not None:
            items.append(BudgetItem(name=f"住宿（{stay['dest']}）",
                                    amount=round(stay["nightly_price"] * stay["nights"], 2),
                                    source="cheapest"))
    items.append(BudgetItem(name="市内与餐饮", amount=round(CITY_MEAL_RATIO * budget_total, 2),
                            source="estimate"))
    total = round(sum(i.amount for i in items), 2)
    budget = BudgetBreakdown(items=items, total=total, budget_total=budget_total,
                             over_budget=total > budget_total)

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
    )
    return Envelope(ok=True, data=plan, degraded="；".join(notes) if notes else None)


register(CapabilityMeta(
    name="compose_itinerary",
    description="组合多目的地调研行程：去回程交通取最低价并留备选、住宿按天数滚动分段"
                "（日期一律 strptime+timedelta，跨月跨年不拼接）、30/50/20 调研节奏与缓冲日、"
                "预算条目（交通/住宿/市内与餐饮估算）与超支标记；单腿无价只降级不报错。",
    fn=compose_itinerary,
    args_model=ComposeItineraryArgs,
))
