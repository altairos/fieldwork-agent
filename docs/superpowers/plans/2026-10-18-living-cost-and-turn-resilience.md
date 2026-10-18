# 生活成本区间与回合健壮性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「餐饮/市内交通按 ¥150/天」改为「聚落类型 × 就餐方式」的表驱动区间，并修掉「过去日期被拒 + 前导语悬空」导致的回合卡死，附「重新生成」入口。

**Architecture:** 新增纯函数能力 `estimate_living_cost`（内核表值，唯一生活成本来源），`compose_itinerary` 复用之并把预算条目拆成餐饮/市内交通两条带区间；`BudgetItem`/`BudgetBreakdown` 增加区间字段与 `over_budget_possible`。回合侧在 system prompt 注入今日日期与成本口径、搜索原语前置日期校验、loop 识别前导语后带工具重问一次、仓库层新增 `pop_last_message` 支撑 `POST /turns/{id}/retry`。

**Tech Stack:** Python 3.11+（pydantic v2、FastAPI、pytest）、React 18 + TypeScript + Zustand + vitest。

**Spec:** `docs/superpowers/specs/2026-10-18-living-cost-and-turn-resilience-design.md`

## Global Constraints

- 新增能力必须同时登记 `kernel/__init__.py`（聚合导入）与 `core/orchestrator.py::_CAPABILITY_DISPATCH`；`tests/test_loop_orchestrator.py::test_capability_dispatch_covers_all_registered_tools` 断言两者键集**全等**，漏一处即红。
- 餐饮/市内交通一律返回区间（`amount_low`/`amount_high`），不得再引入 `150.0` 这类单点日标准常量；`meals_local_per_person_day` 仅作人工覆盖入口且默认 `None`。
- `coverage != "complete"` 时 `over_budget` 与 `over_budget_possible` 一律为 `False`；前端不得在跨预算线时显示「预算内」。
- 测试全 mock，不联网、不调真实 flyai/LLM。每任务收尾跑 `python -m pytest <目标文件> -v`；前端任务跑 `cd web; npx vitest run tests/<file>`，最后全量 `python -m pytest`、`ruff check .`、`cd web; npx tsc --noEmit; npm test`。
- 只改本任务 Files 下列出的文件；`git add` 必须逐文件写全路径，禁止 `git add -A`。
- 注释与文案用中文，密度对齐周边模块。
- **提交时机（用户 2026-10-18 指示「等实现完再提交」）**：各任务末尾的提交步骤**本阶段一律不执行**。全部 11 个任务通过最终验收后，才按「最终提交」一节的命令合并为一次提交；期间只用测试作为任务完成判据。

---

## Task 1: 生活成本区间估算能力

**Files:**
- Create: `kernel/aggregation/living_cost.py`
- Modify: `kernel/__init__.py:11-16`（聚合导入加 `living_cost`）
- Modify: `core/orchestrator.py:86-108`（`_CAPABILITY_DISPATCH` 加一行）
- Test: `tests/test_living_cost.py`

**Interfaces:**
- Produces:
  - `estimate_living_cost(settlement_type="village", board_mode="mixed", days=0, people=1, include_local_transport=True) -> Envelope`
  - 成功时 `Envelope.data` 为 dict：`{settlement_type, board_mode, people, days, caliber, items[], total_low, total_high, assumptions[], basis, source, coverage}`；`items` 每项 `{name, per_day_low, per_day_high, amount_low, amount_high, unit}`
  - 模块常量 `SETTLEMENT_TYPES`、`BOARD_MODES`、`FOOD_RANGE`、`TRANSPORT_RANGE`、`BASIS`
- 无 `client`/`llm`/`amap_client` 注入参数（纯函数）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_living_cost.py
from fieldwork_agent.kernel.aggregation.living_cost import estimate_living_cost
from fieldwork_agent.kernel.registry import build_tool_schemas


def test_village_self_cook_sixty_days():
    env = estimate_living_cost("village", "self_cook", days=60)
    assert env.ok is True
    items = {i["name"]: i for i in env.data["items"]}
    assert items["餐饮"]["amount_low"] == 12.0 * 60
    assert items["餐饮"]["amount_high"] == 20.0 * 60
    assert items["市内交通"]["amount_low"] == 0.0
    assert items["市内交通"]["amount_high"] == 5.0 * 60
    assert env.data["total_low"] == 720.0
    assert env.data["total_high"] == 1500.0
    assert env.data["caliber"] == "村内自炊 · 1 人 × 60 天"
    assert "14.7" in env.data["basis"]
    assert env.data["coverage"] == "estimate" and env.data["source"] == "estimate"


def test_transport_can_be_excluded():
    env = estimate_living_cost("county", "eat_out", days=10,
                               include_local_transport=False)
    assert [i["name"] for i in env.data["items"]] == ["餐饮"]
    assert env.data["total_low"] == env.data["items"][0]["amount_low"]


def test_people_multiplies_linearly():
    one = estimate_living_cost("village", "mixed", days=30, people=1)
    three = estimate_living_cost("village", "mixed", days=30, people=3)
    assert three.data["total_low"] == round(one.data["total_low"] * 3, 2)
    assert three.data["total_high"] == round(one.data["total_high"] * 3, 2)


def test_enums_and_bounds_rejected():
    cases = [
        {"settlement_type": "hamlet"},
        {"board_mode": "delivery"},
        {"days": 0},
        {"days": 366},
        {"people": 0},
        {"people": 21},
    ]
    base = {"days": 10}
    for extra in cases:
        env = estimate_living_cost(**{**base, **extra})
        assert env.ok is False
        assert env.error.code == "invalid_arguments"
        assert env.error.hint and "village" in env.error.hint


def test_city_eat_out_far_above_village_self_cook():
    cheap = estimate_living_cost("village", "self_cook", days=10)
    dear = estimate_living_cost("city", "eat_out", days=10)
    assert dear.data["total_low"] > cheap.data["total_high"]


def test_registered_schema_without_injection_params():
    schema = {t["function"]["name"]: t["function"] for t in build_tool_schemas()}
    assert "estimate_living_cost" in schema
    props = schema["estimate_living_cost"]["parameters"]["properties"]
    assert "client" not in props and "llm" not in props
    assert props["settlement_type"]["default"] == "village"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_living_cost.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fieldwork_agent.kernel.aggregation.living_cost'`

- [ ] **Step 3: 实现模块**

```python
# kernel/aggregation/living_cost.py
"""生活成本区间估算 — ``estimate_living_cost`` 能力（表驱动，无 I/O、无 LLM）。

生产会话曾把驻村餐饮按 ¥100–150/人·天估算（城市差旅口径），较云南农村实际高
4–8 倍。本模块用「聚落类型 × 就餐方式」的静态区间表给出可解释估算，金额一律
区间而非单点，并随金额返回口径（caliber）与依据（basis）。

表值为西部农村/县域基线：云南农村居民人均食品烟酒 5,351 元/年（≈14.7 元/人·天，
自炊口径）、人均消费支出 16,214 元/年（≈44.4 元/人·天，含居住）。村内自炊/搭伙
按此上浮取值，城市外食含游客溢价。表值不是实时行情。
"""

from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from pydantic import BaseModel, Field

SETTLEMENT_TYPES = ("village", "town", "county", "city")
BOARD_MODES = ("self_cook", "shared_meal", "mixed", "eat_out")

_SETTLEMENT_LABELS = {
    "village": "村内", "town": "镇上", "county": "县城", "city": "城市",
}
_BOARD_LABELS = {
    "self_cook": "自炊", "shared_meal": "搭伙", "mixed": "混合", "eat_out": "外食",
}

# 餐饮区间（元/人·天）：FOOD_RANGE[聚落][就餐方式] = (下沿, 上沿)
FOOD_RANGE: dict[str, dict[str, tuple[float, float]]] = {
    "village": {"self_cook": (12.0, 20.0), "shared_meal": (15.0, 25.0),
                "mixed": (18.0, 30.0), "eat_out": (25.0, 45.0)},
    "town": {"self_cook": (15.0, 25.0), "shared_meal": (18.0, 28.0),
             "mixed": (22.0, 38.0), "eat_out": (30.0, 50.0)},
    "county": {"self_cook": (18.0, 30.0), "shared_meal": (22.0, 35.0),
               "mixed": (28.0, 45.0), "eat_out": (35.0, 60.0)},
    "city": {"self_cook": (25.0, 40.0), "shared_meal": (28.0, 45.0),
             "mixed": (35.0, 60.0), "eat_out": (50.0, 90.0)},
}

# 市内交通（元/人·天）：村内步行/电动车为主，进城班车单列在大交通
TRANSPORT_RANGE: dict[str, tuple[float, float]] = {
    "village": (0.0, 5.0), "town": (3.0, 10.0),
    "county": (5.0, 15.0), "city": (8.0, 25.0),
}

BASIS = ("云南农村居民人均食品烟酒 5,351 元/年（≈14.7 元/人·天，自炊口径）、"
         "人均消费支出 16,214 元/年（≈44.4 元/人·天，含居住）；村内自炊/搭伙按此"
         "上浮取值，城市外食含游客溢价。表值为静态基线，不是当地实时行情。")

_ASSUMPTIONS = {
    "self_cook": "自炊按在家做饭、不含酒水",
    "shared_meal": "搭伙按与房东/农户搭伙、不含酒水",
    "mixed": "混合按部分自炊、部分在外解决",
    "eat_out": "外食按当地小馆/食堂，不含宴请",
}


class EstimateLivingCostArgs(BaseModel):
    """``estimate_living_cost`` 入参（description 即模型选值指引）。"""

    settlement_type: str = Field(
        default="village",
        description="聚落类型：village 村 / town 镇 / county 县城 / city 城市；驻村调研通常为 village")
    board_mode: str = Field(
        default="mixed",
        description="就餐方式：self_cook 自炊 / shared_meal 搭伙 / mixed 混合 / eat_out 外食；"
                    "能做饭选 self_cook，与农户搭伙选 shared_meal")
    days: int = Field(description="驻留天数（1-365）")
    people: int = Field(default=1, description="人数（1-20）")
    include_local_transport: bool = Field(
        default=True, description="是否同时估算市内交通（村内日常通勤/短途）")


def _invalid(message: str) -> Envelope:
    return Envelope(ok=False, error=ToolError(
        code="invalid_arguments", message=message,
        hint=("settlement_type 取 " + "/".join(SETTLEMENT_TYPES)
              + "；board_mode 取 " + "/".join(BOARD_MODES)
              + "；days 取 1-365；people 取 1-20")))


def estimate_living_cost(settlement_type: str = "village", board_mode: str = "mixed",
                         days: int = 0, people: int = 1,
                         include_local_transport: bool = True) -> Envelope:
    """按「聚落类型 × 就餐方式」估算餐饮与市内交通区间（元）。"""
    if settlement_type not in SETTLEMENT_TYPES:
        return _invalid(f"未知聚落类型：{settlement_type}")
    if board_mode not in BOARD_MODES:
        return _invalid(f"未知就餐方式：{board_mode}")
    if not 1 <= days <= 365:
        return _invalid(f"days 必须在 1-365 之间，收到 {days}")
    if not 1 <= people <= 20:
        return _invalid(f"people 必须在 1-20 之间，收到 {people}")

    food_low, food_high = FOOD_RANGE[settlement_type][board_mode]
    items = [{
        "name": "餐饮",
        "per_day_low": food_low, "per_day_high": food_high,
        "amount_low": round(food_low * people * days, 2),
        "amount_high": round(food_high * people * days, 2),
        "unit": "元/人·天×人数×天数",
    }]
    assumptions = [_ASSUMPTIONS[board_mode]]
    if include_local_transport:
        tr_low, tr_high = TRANSPORT_RANGE[settlement_type]
        items.append({
            "name": "市内交通",
            "per_day_low": tr_low, "per_day_high": tr_high,
            "amount_low": round(tr_low * people * days, 2),
            "amount_high": round(tr_high * people * days, 2),
            "unit": "元/人·天×人数×天数",
        })
        assumptions.append("市内交通不含城际往返（大交通另计）")

    return Envelope(ok=True, data={
        "settlement_type": settlement_type,
        "board_mode": board_mode,
        "people": people,
        "days": days,
        "caliber": (f"{_SETTLEMENT_LABELS[settlement_type]}"
                    f"{_BOARD_LABELS[board_mode]} · {people} 人 × {days} 天"),
        "items": items,
        "total_low": round(sum(i["amount_low"] for i in items), 2),
        "total_high": round(sum(i["amount_high"] for i in items), 2),
        "assumptions": assumptions,
        "basis": BASIS,
        "source": "estimate",
        "coverage": "estimate",
    })


register(CapabilityMeta(
    name="estimate_living_cost",
    description="生活成本区间估算（餐饮/市内交通）：按聚落类型 × 就餐方式查表，"
                "返回区间与口径，不返回单点精确值。驻村场景用 village + "
                "self_cook/shared_meal；不得把城市游客标准套到村里。",
    fn=estimate_living_cost,
    args_model=EstimateLivingCostArgs,
    read_only=True,
))
```

- [ ] **Step 4: 注册到聚合导入与 dispatch 表**

`kernel/__init__.py` 的 aggregation 导入列表加 `living_cost`（字母序放在 `last_mile` 前）：

```python
from fieldwork_agent.kernel.aggregation import (  # noqa: F401
    itinerary,
    last_mile,
    living_cost,
    site_profile,
    window,
)
```

`core/orchestrator.py` 顶部导入：

```python
from fieldwork_agent.kernel.aggregation.living_cost import estimate_living_cost
```

`_CAPABILITY_DISPATCH` 在 `"analyze_fieldwork_window"` 之后加：

```python
    # 纯函数能力：无外部客户端注入（inject 为空串 → dispatch 不注入任何 kwargs）
    "estimate_living_cost": (estimate_living_cost, ""),
```

**连带同步（实现时发现，计划初稿漏列）**：MCP 侧工具组表与计数断言随新增能力更新——
`mcp_server/mapping.py` 的 `TOOL_GROUPS` 加 `"estimate_living_cost": "aggregation"`；
`tests/test_mcp_mapping.py`（21→22 能力、TOOL_GROUPS 21→22）、`tests/test_mcp_dispatch.py`
（21→22 且新增纯函数接缝断言）、`tests/test_mcp_http.py` 与 `tests/test_mcp_server_contract.py`
（EXPECTED 30→31 工具）同步改。

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_living_cost.py tests/test_registry_surface.py tests/test_loop_orchestrator.py -v`
Expected: PASS（含 `test_capability_dispatch_covers_all_registered_tools`）

- [ ] **Step 6: 提交**

```bash
git add kernel/aggregation/living_cost.py kernel/__init__.py core/orchestrator.py tests/test_living_cost.py
git commit -m "feat(kernel): 表驱动生活成本区间估算 estimate_living_cost"
```

---

## Task 2: 预算契约区间字段 + compose_itinerary 复用表值

**Files:**
- Modify: `kernel/models.py`（`BudgetItem`、`BudgetBreakdown`）
- Modify: `kernel/aggregation/itinerary.py`
- Test: `tests/test_itinerary.py:122-140`、`:174-178`（改既有断言）+ 新增用例
- Test: `tests/test_budget_contract.py`（新建）

**Interfaces:**
- Consumes: `estimate_living_cost`、`FOOD_RANGE`、`TRANSPORT_RANGE`（Task 1）
- Produces:
  - `BudgetItem.amount_low: float | None`、`amount_high: float | None`、`basis: str`
  - `BudgetBreakdown.total_high: float | None`、`over_budget_possible: bool`
  - `ComposeItineraryArgs.settlement_type: str | None`、`board_mode: str | None`、`include_local_transport: bool`、`meals_local_per_person_day: float | None`
  - 预算条目名：`餐饮（估算）`、`市内交通（估算）`

- [ ] **Step 1: 写失败测试（契约字段）**

```python
# tests/test_budget_contract.py
from fieldwork_agent.kernel.models import BudgetBreakdown, BudgetItem


def test_budget_item_range_fields_default_none():
    item = BudgetItem(name="交通", amount=350.0, source="cheapest")
    assert item.amount_low is None
    assert item.amount_high is None
    assert item.basis == ""


def test_budget_breakdown_range_fields_default():
    b = BudgetBreakdown(total=100.0, budget_total=200.0, coverage="complete")
    assert b.total_high is None
    assert b.over_budget_possible is False


def test_old_payload_deserializes():
    b = BudgetBreakdown.model_validate({
        "items": [{"name": "住宿", "amount": 13500}],
        "total": 13500, "budget_total": 20000,
        "coverage": "partial", "uncovered": ["餐饮"],
    })
    assert b.items[0].amount_high is None
    assert b.total_high is None and b.over_budget_possible is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_budget_contract.py -v`
Expected: FAIL — `BudgetItem` 无 `amount_low` 字段 / pydantic 忽略未知键后断言失败

- [ ] **Step 3: 扩展契约模型**

`kernel/models.py` 的 `BudgetItem`（`source_ref` 之后追加）：

```python
    amount_low: float | None = None    # 估算区间下沿（amount 已等于下沿）
    amount_high: float | None = None   # 估算区间上沿；报价类条目为 None
    basis: str = ""                    # 估算口径与数据依据（报价类留空）
```

`BudgetBreakdown`（`uncovered` 之后追加）：

```python
    total_high: float | None = None            # 含估算上沿的合计；报价类条目 low=high
    over_budget_possible: bool = False         # total_high > budget_total：区间跨预算线
```

- [ ] **Step 4: 改造 compose_itinerary**

`kernel/aggregation/itinerary.py` 顶部导入：

```python
from fieldwork_agent.kernel.aggregation.living_cost import (
    BOARD_MODES,
    SETTLEMENT_TYPES,
    estimate_living_cost,
)
```

常量区把 `MEALS_PER_PERSON_DAY_DEFAULT = 150.0`（第 77 行）整段替换为：

```python
# 默认生活成本口径：村内混合（自炊+搭伙）；表值见 living_cost.FOOD_RANGE
DEFAULT_SETTLEMENT = "village"
DEFAULT_BOARD_MODE = "mixed"
```

`ComposeItineraryArgs` 增加/替换字段（`meals_local_per_person_day` 行改掉，追加三行）：

```python
    settlement_type: str | None = Field(
        default=None, description="聚落类型 village/town/county/city；驻村通常 village")
    board_mode: str | None = Field(
        default=None, description="就餐方式 self_cook/shared_meal/mixed/eat_out")
    include_local_transport: bool = Field(
        default=True, description="是否把市内交通作为独立估算条目")
    meals_local_per_person_day: float | None = Field(
        default=None, description="人工指定人均日餐标（元/人·天）；不填则按聚落与就餐方式查表")
```

注意 `itinerary.py` 目前用裸 `from pydantic import BaseModel`，需改为 `from pydantic import BaseModel, Field`。

`_validate` 追加两个入参与枚举校验（调用处同步传参）：

```python
def _validate(departure: str, destinations: list[str], duration_days: int,
              budget_total: float, researcher_count: int,
              settlement_type: str = "", board_mode: str = "") -> Envelope | None:
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
    if settlement_type and settlement_type not in SETTLEMENT_TYPES:
        problems.append(f"settlement_type 必须是 {'/'.join(SETTLEMENT_TYPES)} 之一")
    if board_mode and board_mode not in BOARD_MODES:
        problems.append(f"board_mode 必须是 {'/'.join(BOARD_MODES)} 之一")
    if not problems:
        return None
    return _error("bad_input", "；".join(problems),
                  "请给出出发地、至少一个目的地、YYYY-MM-DD 起始日期、正整数天数与正数预算")
```

`compose_itinerary` 函数签名（第 291-295 行）替换为：

```python
def compose_itinerary(departure: str, destinations: list[str], start_date: str,
                      duration_days: int, budget_total: float, researcher_count: int = 1,
                      rooms: int = 0, settlement_type: str | None = None,
                      board_mode: str | None = None,
                      include_local_transport: bool = True,
                      meals_local_per_person_day: float | None = None,
                      *, client: FlyAIClient | None = None,
                      amap_client: AMapClient | None = None) -> Envelope:
```

函数体内 `bad = _validate(...)` 调用改为：

```python
    bad = _validate(departure, destinations, duration_days, budget_total,
                    researcher_count, settlement_type or "", board_mode or "")
```

预算段（第 468-471 行的 `meal_amount` 三行）整段替换为：

```python
    # 生活成本（表驱动区间）：单一装配路径，meal override 只替换餐饮金额；
    # 未指定口径时按默认村内混合并在 basis 前缀标注
    st = settlement_type or DEFAULT_SETTLEMENT
    bm = board_mode or DEFAULT_BOARD_MODE
    living = estimate_living_cost(settlement_type=st, board_mode=bm,
                                  days=duration_days, people=researcher_count,
                                  include_local_transport=include_local_transport)
    data = living.data if isinstance(living.data, dict) else {}
    default_note = ("默认口径（调用方未指定聚落类型/就餐方式）；"
                    if not (settlement_type and board_mode) else "")
    basis = f"{default_note}{data.get('caliber', '')}；{data.get('basis', '')}"
    for entry in data.get("items", []):
        if entry["name"] == "餐饮" and meals_local_per_person_day is not None:
            amount = round(float(meals_local_per_person_day)
                           * researcher_count * duration_days, 2)
            items.append(BudgetItem(
                name="餐饮（估算）", amount=amount,
                amount_low=amount, amount_high=amount, source="estimate",
                unit=entry["unit"], quantity=float(duration_days),
                people=researcher_count,
                basis=(f"人工指定人均日标准 {meals_local_per_person_day} 元/人·天；"
                       f"{default_note}{data.get('caliber', '')}")))
            continue
        items.append(BudgetItem(
            name=f"{entry['name']}（估算）", amount=entry["amount_low"],
            amount_low=entry["amount_low"], amount_high=entry["amount_high"],
            source="estimate", unit=entry["unit"],
            quantity=float(duration_days), people=researcher_count,
            basis=basis))
```

`total` 与 `BudgetBreakdown` 构造（第 472-482 行）替换为：

```python
    total = round(sum(i.amount for i in items), 2)
    total_high = round(sum(i.amount_high if i.amount_high is not None else i.amount
                           for i in items), 2)
    # coverage：全部预期报价可得=complete；部分可得=partial；一条都没有=unknown
    if not uncovered:
        coverage = "complete"
    elif priced_count > 0:
        coverage = "partial"
    else:
        coverage = "unknown"
    budget = BudgetBreakdown(
        items=items, total=total, total_high=total_high, budget_total=budget_total,
        over_budget=coverage == "complete" and total > budget_total,
        over_budget_possible=coverage == "complete" and total_high > budget_total,
        coverage=coverage, uncovered=uncovered)
```

`inputs_hash` 的 dict 追加口径键（放在 `"rooms": rooms_eff` 之后）：

```python
            "settlement_type": settlement_type or DEFAULT_SETTLEMENT,
            "board_mode": board_mode or DEFAULT_BOARD_MODE,
            "include_local_transport": include_local_transport,
```

模块文档字符串第 18-22 行「``市内与餐饮`` = 人均日标准…默认 150」改为：

```
  （逐腿计，多目的地不低估）；住宿 = 每晚最低价 × 该点夜数 × 房间数（默认 2 人/间
  假设，``rooms`` 可调）；餐饮与市内交通 = 按「聚落类型 × 就餐方式」查表得到的
  人均日区间 × 人数 × 天数（estimate，见 ``living_cost.py``），不再有单点日标准；
```

- [ ] **Step 5: 更新既有断言 + 新增用例**

`tests/test_itinerary.py` 的 `test_budget_items_formula_and_over_budget_flag`（122-140 行）整段替换：

```python
def test_budget_items_formula_and_over_budget_flag():
    """R2 口径：交通=单价×人数、住宿=晚价×夜数×房间数、餐饮/市内交通=表值区间×人数×天数。"""
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 30, 20000, client=StubClient())
    b = env.data.budget
    by_name = {i.name: i for i in b.items}
    assert by_name["交通（昆明→大理）"].amount == 350.0       # 去程腿
    assert by_name["交通（大理→昆明）"].amount == 350.0       # 返程腿
    assert by_name["住宿（大理）"].amount == 150.0 * 29       # 150/晚 × 29 夜 × 1 间
    food = by_name["餐饮（估算）"]
    assert food.amount == 18.0 * 1 * 30                      # village+mixed 下沿
    assert food.amount_low == 18.0 * 30 and food.amount_high == 30.0 * 30
    assert food.source == "estimate" and food.unit == "元/人·天×人数×天数"
    assert "默认口径" in food.basis and "14.7" in food.basis
    local = by_name["市内交通（估算）"]
    assert local.amount == 0.0 and local.amount_high == 5.0 * 30
    assert all(i.currency == "CNY" for i in b.items)
    assert all(i.source in ("cheapest", "estimate") for i in b.items)
    assert b.total == 700.0 + 4350.0 + 540.0 + 0.0
    assert b.total_high == 700.0 + 4350.0 + 900.0 + 150.0
    assert b.over_budget is False and b.over_budget_possible is False
    assert b.coverage == "complete"

    tight = compose_itinerary("昆明", ["大理"], "2026-11-01", 30, 1000, client=StubClient())
    assert tight.data.budget.budget_total == 1000
    assert tight.data.budget.over_budget is True          # 下沿已超
    assert tight.data.budget.over_budget_possible is True


def test_explicit_meal_override_replaces_meal_range_only():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                            meals_local_per_person_day=30, client=StubClient())
    by_name = {i.name: i for i in env.data.budget.items}
    food = by_name["餐饮（估算）"]
    assert food.amount_low == food.amount_high == 30.0 * 10
    assert "人工指定" in food.basis
    # override 只作用于餐标：市内交通仍按表值区间（village 0-5 元/人·天）
    assert by_name["市内交通（估算）"].amount_high == 5.0 * 10


def test_settlement_and_board_mode_shift_amounts():
    cheap = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                              settlement_type="village", board_mode="self_cook",
                              client=StubClient())
    dear = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                             settlement_type="city", board_mode="eat_out",
                             client=StubClient())
    a = next(i for i in cheap.data.budget.items if i.name == "餐饮（估算）")
    b = next(i for i in dear.data.budget.items if i.name == "餐饮（估算）")
    assert b.amount_low > a.amount_high
    assert "默认口径" not in b.basis


def test_local_transport_can_be_excluded():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                            include_local_transport=False, client=StubClient())
    assert not any(i.name == "市内交通（估算）" for i in env.data.budget.items)


def test_invalid_settlement_rejected():
    env = compose_itinerary("昆明", ["大理"], "2026-11-01", 10, 20000,
                            settlement_type="hamlet", client=StubClient())
    assert env.ok is False and env.error.code == "bad_input"
    assert "settlement_type" in env.error.message
```

`test_budget_scales_with_researcher_count`（174-178 行）的断言改为：

```python
    assert by_name["餐饮（估算）"] == 18.0 * 3 * 10            # village+mixed 下沿×人数×天数
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_itinerary.py tests/test_budget_contract.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add kernel/models.py kernel/aggregation/itinerary.py tests/test_itinerary.py tests/test_budget_contract.py
git commit -m "feat(budget): 预算条目区间化，compose_itinerary 复用表驱动生活成本"
```

---

## Task 3: 成本口径提示词（单一来源）

**Files:**
- Create: `kernel/guidance/costs.py`
- Modify: `web_host/agent/context.py:6-27`（`_TOOL_GUIDANCE` 不变，新增 import 与拼装）
- Modify: `core/orchestrator.py:116-131`（新增 import 与 `_system_prompt` 抽取）
- Test: `tests/test_guidance_cost.py`

**Interfaces:**
- Produces: `COST_GUIDANCE: str`；`core.orchestrator._system_prompt(phase: str, snapshot: str) -> str`
- Consumes: `phases.phase_prompt`（既有）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_guidance_cost.py
from fieldwork_agent.kernel.guidance.costs import COST_GUIDANCE


def test_cost_guidance_names_capability_and_contract():
    assert "estimate_living_cost" in COST_GUIDANCE
    assert "区间" in COST_GUIDANCE and "待核验" in COST_GUIDANCE
    assert "城市差旅" in COST_GUIDANCE


def test_web_system_prompt_includes_cost_guidance():
    from fieldwork_agent.web_host.agent.context import build_system_prompt

    prompt = build_system_prompt("research_positioning", "项目快照")
    assert COST_GUIDANCE in prompt


def test_cli_system_prompt_includes_cost_guidance():
    from fieldwork_agent.core.orchestrator import _system_prompt

    assert COST_GUIDANCE in _system_prompt("research_positioning", "项目快照")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_guidance_cost.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fieldwork_agent.kernel.guidance.costs'`

- [ ] **Step 3: 新建提示词模块**

```python
# kernel/guidance/costs.py
"""成本口径提示词（单一来源）：生活成本数字必须先经工具估算。"""

COST_GUIDANCE = (
    "成本口径：任何餐饮、生活费、市内交通的金额估算，必须先调用 estimate_living_cost"
    "（或 compose_itinerary，其预算条目已带区间与口径）；报数字时必须同时给区间、"
    "口径（聚落类型/就餐方式/人数/天数）与假设。驻村场景默认按村内自炊或搭伙，"
    "不得套用城市差旅、游客或酒店标准；工具未返回的金额一律标「待核验」，"
    "不得给出单点精确值，也不得把用户随口说的数字改写成工具输出。"
)
```

- [ ] **Step 4: 两处 system 拼装接入**

`web_host/agent/context.py` 顶部：

```python
from fieldwork_agent.kernel.guidance.costs import COST_GUIDANCE
```

`build_system_prompt`：

```python
def build_system_prompt(phase: str, snapshot: str) -> str:
    return "\n\n".join([phases.phase_prompt(phase), COST_GUIDANCE,
                        _TOOL_GUIDANCE, snapshot])
```

`core/orchestrator.py` 顶部：

```python
from fieldwork_agent.kernel.guidance.costs import COST_GUIDANCE
```

`_TOOL_GUIDANCE` 之后新增：

```python
def _system_prompt(phase: str, snapshot: str) -> str:
    """CLI 回合 system（与 web_host/agent/context.py 同序拼装）。"""
    return "\n\n".join([phases.phase_prompt(phase), COST_GUIDANCE,
                        _TOOL_GUIDANCE, snapshot])
```

`FieldAgent.chat` 第 246-247 行替换为：

```python
            system = _system_prompt(phase, self._snapshot(pid))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_guidance_cost.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add kernel/guidance/costs.py web_host/agent/context.py core/orchestrator.py tests/test_guidance_cost.py
git commit -m "feat(guidance): 成本口径提示词统一接入 web 与 CLI system"
```

---

## Task 4: 工具卡失败原因透传

**Files:**
- Modify: `web/src/components/cards/types.ts`
- Modify: `web/src/components/chat/Timeline.tsx:97-104`
- Modify: `web/src/components/cards/SearchResultCard.tsx:146-196`
- Test: `web/tests/toolCards.spec.tsx`

**Interfaces:**
- Produces: `ToolCardProps.error?: { code?: string; message?: string; hint?: string } | null`
- 失败空态文案优先级：`data.summary.hints[hintKey]` → `error.hint` → `error.message` → 现有默认文案。

- [ ] **Step 1: 写失败测试**

追加到 `web/tests/toolCards.spec.tsx` 末尾：

```tsx
describe("SearchResultCard 失败原因透传", () => {
  test("error.hint 优先于通用无供给文案", () => {
    render(<TrainCard data={null} error={{
      code: "flyai_invalid_date",
      message: "出发日期非法",
      hint: "实时搜索仅支持今天（2026-10-18）及以后的日期；请改用 2027-07-15 重试",
    }} />);
    expect(screen.getByText(/仅支持今天（2026-10-18）及以后/)).toBeInTheDocument();
    expect(screen.queryByText(/本次查询无可用供给/)).not.toBeInTheDocument();
  });

  test("只有 message 时展示 message", () => {
    render(<TrainCard data={null} error={{ code: "flyai_error", message: "服务暂不可用" }} />);
    expect(screen.getByText(/服务暂不可用/)).toBeInTheDocument();
  });

  test("无 error 时保持原文案", () => {
    render(<TrainCard data={null} />);
    expect(screen.getByText(/本次查询无可用供给/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web; npx vitest run tests/toolCards.spec.tsx`
Expected: FAIL — `error` 不是 `TrainCard` 的合法 prop（TS 报错）/ 断言找不到 hint 文案

- [ ] **Step 3: 扩展卡片 props**

`web/src/components/cards/types.ts` 整体替换为：

```ts
/**
 * 卡片层公共类型 — 单独成模块以打破 `registry.tsx ↔ 各卡片` 的循环类型依赖
 * （卡片 import ToolCardProps、registry import 卡片组件）。
 * `registry.tsx` 原样 re-export，调用方仍可从 registry 取用（brief 契约不变）。
 */

/** 工具失败原因（`Envelope.error` 的 JSON 形态；三字段都可能缺省）。 */
export interface ToolErrorView {
  code?: string;
  message?: string;
  hint?: string;
}

/** 每张工具卡的统一 props：payload 原样透传（形状未知 → 卡内自行容错解析）。 */
export interface ToolCardProps {
  /** `tool_completed.data`：内核 Envelope.data 的 JSON 形态，可能为 null（工具失败/降级）。 */
  data: unknown;
  /** `Envelope.degraded`：降级原因原文（如 "flyai_unavailable"），有值即需在卡头标注。 */
  degraded?: string | null;
  /** `Envelope.error`：失败原因；失败卡据此给出可操作提示而不是泛化空态。 */
  error?: ToolErrorView | null;
}
```

- [ ] **Step 4: Timeline 传入 error**

`web/src/components/chat/Timeline.tsx` 第 99-103 行的 `<Card … />` 加一行：

```tsx
              <Card
                key={call.call_id}
                data={call.data === undefined ? null : call.data}
                degraded={call.degraded ?? null}
                error={call.error ?? null}
              />
```

- [ ] **Step 5: 失败空态优先展示原因**

`web/src/components/cards/SearchResultCard.tsx`：

函数签名解构加 `error`（`SearchResultCardProps extends ToolCardProps`，接口本身不用改）：

```tsx
export function SearchResultCard({
  data,
  degraded,
  error,
  title,
  icon,
  emptyTitle,
  emptyNext,
  source = "search",
  thumb = false,
  unitSuffix,
  hintKey,
}: SearchResultCardProps) {
```

`kernelHint` 之后加：

```tsx
  // 失败原因优先于通用空态：hint 是可操作下一步（如改用未来日期），message 是事实描述
  const failureHint = (error?.hint || error?.message || "").trim();
```

空态 `Notice` 改为：

```tsx
          <Notice tone="ochre">
            {kernelHint ||
              failureHint ||
              `${
                degraded ? "数据源本次未返回有效结果" : "本次查询无可用供给"
              }：该条不计入预算，请人工核验。下一步：${emptyNext}`}
          </Notice>
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd web; npx vitest run tests/toolCards.spec.tsx; npx tsc --noEmit`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add web/src/components/cards/types.ts web/src/components/chat/Timeline.tsx web/src/components/cards/SearchResultCard.tsx web/tests/toolCards.spec.tsx
git commit -m "feat(web): 工具卡透传 error，搜索失败卡展示可操作原因"
```

---

## Task 5: 预算区间渲染与生活成本卡

**Files:**
- Modify: `web/src/components/cards/BudgetCard.tsx`
- Create: `web/src/components/cards/LivingCostCard.tsx`
- Modify: `web/src/components/cards/registry.tsx:45-65`、`:81-100`
- Test: `web/tests/toolCards.spec.tsx`（BudgetCard 区间）
- Test: `web/tests/livingCostCard.spec.tsx`（新建）

**Interfaces:**
- Consumes: `BudgetItem.amount_low/amount_high/basis`、`BudgetBreakdown.total_high/over_budget_possible`（Task 2）
- Produces: `LivingCostCard`（注册键 `estimate_living_cost`）；`toBudget` 返回值新增 `total_high`、`over_budget_possible`、条目级三字段

- [ ] **Step 1: 写失败测试**

追加到 `web/tests/toolCards.spec.tsx` 末尾：

```tsx
describe("BudgetCard 估算区间", () => {
  const RANGE_BUDGET = {
    items: [
      { name: "住宿（大理）", amount: 4350, source: "cheapest", currency: "CNY",
        unit: "每晚价×夜数×房间数", quantity: 29, people: 1, priced_at: "", source_ref: "" },
      { name: "餐饮（估算）", amount: 540, amount_low: 540, amount_high: 900,
        source: "estimate", currency: "CNY", unit: "元/人·天×人数×天数",
        quantity: 30, people: 1, priced_at: "", basis: "村内混合；云南农村人均食品烟酒 5,351 元/年" },
    ],
    total: 4890, total_high: 5250, budget_total: 20000,
    over_budget: false, over_budget_possible: false,
    coverage: "complete", uncovered: [],
  };

  test("条目渲染区间与口径", () => {
    render(<BudgetCard data={RANGE_BUDGET} />);
    expect(screen.getByText("¥540–¥900")).toBeInTheDocument();
    expect(screen.getByText(/云南农村人均食品烟酒/)).toBeInTheDocument();
    expect(screen.getByText(/上限约 ¥5250/)).toBeInTheDocument();
  });

  test("区间跨预算线显示「跨预算线」且不显示「预算内」", () => {
    render(<BudgetCard data={{ ...RANGE_BUDGET, budget_total: 5000, over_budget_possible: true }} />);
    expect(screen.getByText("跨预算线")).toBeInTheDocument();
    expect(screen.queryByText("预算内")).not.toBeInTheDocument();
    expect(screen.getByText(/按上限.*可能超出/)).toBeInTheDocument();
  });
});
```

新建 `web/tests/livingCostCard.spec.tsx`：

```tsx
import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { LivingCostCard } from "../src/components/cards/LivingCostCard";

const DATA = {
  settlement_type: "village", board_mode: "self_cook", people: 1, days: 60,
  caliber: "村内自炊 · 1 人 × 60 天",
  items: [
    { name: "餐饮", per_day_low: 12, per_day_high: 20,
      amount_low: 720, amount_high: 1200, unit: "元/人·天×人数×天数" },
    { name: "市内交通", per_day_low: 0, per_day_high: 5,
      amount_low: 0, amount_high: 300, unit: "元/人·天×人数×天数" },
  ],
  total_low: 720, total_high: 1500,
  assumptions: ["自炊按在家做饭、不含酒水"],
  basis: "云南农村居民人均食品烟酒 5,351 元/年",
  source: "estimate", coverage: "estimate",
};

describe("LivingCostCard", () => {
  test("渲染口径、明细区间与合计", () => {
    render(<LivingCostCard data={DATA} />);
    expect(screen.getByText(/村内自炊 · 1 人 × 60 天/)).toBeInTheDocument();
    expect(screen.getByText("¥720–¥1200")).toBeInTheDocument();
    expect(screen.getByText("¥0–¥300")).toBeInTheDocument();
    expect(screen.getByText(/合计.*¥720–¥1500/)).toBeInTheDocument();
  });

  test("渲染假设与依据", () => {
    render(<LivingCostCard data={DATA} />);
    expect(screen.getByText(/不含酒水/)).toBeInTheDocument();
    expect(screen.getByText(/5,351 元\/年/)).toBeInTheDocument();
  });

  test("空 data 落空态", () => {
    render(<LivingCostCard data={null} />);
    expect(screen.getByText(/未取得生活成本估算/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web; npx vitest run tests/toolCards.spec.tsx tests/livingCostCard.spec.tsx`
Expected: FAIL — `LivingCostCard` 模块不存在；BudgetCard 无区间文案

- [ ] **Step 3: 扩展 BudgetCard 视图与渲染**

`BudgetItemView` 加三字段（`source_ref` 之后）：

```ts
  amount_low: number | null;
  amount_high: number | null;
  basis: string;
```

`BudgetView` 加两字段（`uncovered` 之前）：

```ts
  total_high: number | null;
  over_budget_possible: boolean;
```

`toBudget` 的 item 映射加：

```ts
      amount_low: numOf(r.amount_low),
      amount_high: numOf(r.amount_high),
      basis: strOf(r.basis),
```

`toBudget` 返回对象加：

```ts
    total_high: numOf(rec.total_high),
    over_budget_possible: boolOf(rec.over_budget_possible),
```

状态 chip 与 tone（第 170-182 行）替换为：

```tsx
  const crossLine = complete && b.over_budget_possible && !over;
  // 结论色：不完整/跨线 → ochre（不给"预算内"结论）；complete 且超支 → danger；否则 bamboo
  const tone = !complete || crossLine ? "ochre" : over ? "danger" : "bamboo";
  const barColor = tone === "danger" ? "bg-danger" : tone === "ochre" ? "bg-ochre" : "bg-bamboo";
  const pctColor =
    tone === "danger" ? "text-danger" : tone === "ochre" ? "text-ochre" : "text-bamboo";

  const statusChip = !complete ? (
    <Chip tone="ochre">预算不完整</Chip>
  ) : over ? (
    <Chip tone="cinnabar">超出预算</Chip>
  ) : crossLine ? (
    <Chip tone="ochre">跨预算线</Chip>
  ) : (
    <Chip tone="bamboo">预算内</Chip>
  );
```

合计区补上限行（`</div>` 关闭进度条容器之前，即第 213 行后）：

```tsx
            {b.total_high !== null && (
              <div className="text-[11px] text-inkfaint mt-1">
                {`含估算区间，上限约 ${yuan(b.total_high)}`}
              </div>
            )}
```

结论段（第 222-224 行）替换为：

```tsx
          {complete && !over && diff !== null && (
            crossLine ? (
              <div className="text-[11.5px] text-ochre">
                {`按下限低于预算 ${yuan(diff)}，按上限（${yuan(b.total_high)}）可能超出，需按区间预留`}
              </div>
            ) : (
              <div className="text-[11.5px] text-bamboo">{`低于预算上限 ${yuan(diff)}（报价齐备，可据此判断）`}</div>
            )
          )}
```

金额列（第 244-249 行）替换为：

```tsx
                  <span className="shrink-0 inline-flex items-baseline gap-1">
                    {item.amount_low !== null && item.amount_high !== null ? (
                      <span className="num text-[12.5px] text-ink">
                        {`${yuan(item.amount_low)}–${yuan(item.amount_high)}`}
                      </span>
                    ) : (
                      <Price value={item.amount} source={item.source || undefined} />
                    )}
                    {item.currency && item.currency !== "CNY" && (
                      <Chip tone="neutral">{item.currency}</Chip>
                    )}
                  </span>
```

口径副行之后补 basis 行：

```tsx
                    {item.basis && (
                      <div className="text-[10.5px] text-inkfaint truncate" title={item.basis}>
                        {item.basis}
                      </div>
                    )}
```

- [ ] **Step 4: 新建 LivingCostCard**

```tsx
// web/src/components/cards/LivingCostCard.tsx
import { Chip } from "../ui/Chip";
import { IconWallet } from "../ui/icons";
import { CardShell, NoData } from "./CardShell";
import { asArray, asRecord, numOf, strOf, yuan } from "./data";
import type { ToolCardProps } from "./types";

/**
 * LivingCostCard — 生活成本区间估算卡（`estimate_living_cost`）。
 *
 * 红线（价格不失真）：金额一律来自内核表值区间，卡片只做格式化，不换算、不补 0；
 * 口径（聚落/就餐方式/人数/天数）与数据依据必须与金额同屏，避免被当成当地实时行情。
 */

interface CostItemView {
  name: string;
  per_day_low: number | null;
  per_day_high: number | null;
  amount_low: number | null;
  amount_high: number | null;
  unit: string;
}

export function LivingCostCard({ data, degraded }: ToolCardProps) {
  const rec = asRecord(data);
  const items: CostItemView[] = asArray(rec.items).map((raw) => {
    const r = asRecord(raw);
    return {
      name: strOf(r.name),
      per_day_low: numOf(r.per_day_low),
      per_day_high: numOf(r.per_day_high),
      amount_low: numOf(r.amount_low),
      amount_high: numOf(r.amount_high),
      unit: strOf(r.unit),
    };
  });
  const totalLow = numOf(rec.total_low);
  const totalHigh = numOf(rec.total_high);
  const caliber = strOf(rec.caliber);
  const assumptions = asArray(rec.assumptions)
    .map((a) => strOf(a))
    .filter((a) => a !== "");
  const basis = strOf(rec.basis);
  const coverage = strOf(rec.coverage);

  // 空 data / 无明细：如实说明，不臆造金额（红线③）
  if (items.length === 0) {
    return (
      <CardShell
        title="生活成本估算"
        icon={<IconWallet className="w-4 h-4" />}
        degraded={degraded}
      >
        <NoData hint="未取得生活成本估算：本次调用失败或未返回明细；请检查聚落类型/就餐方式参数后重试。" />
      </CardShell>
    );
  }

  return (
    <CardShell
      title="生活成本估算"
      subtitle={caliber || undefined}
      icon={<IconWallet className="w-4 h-4" />}
      degraded={degraded}
      statusChip={coverage === "estimate"
        ? <Chip tone="ochre">表值估算 · 非实时报价</Chip>
        : undefined}
    >
      <ul>
        {items.map((item, i) => (
          <li
            key={`${item.name}-${i}`}
            className="flex items-start justify-between gap-3 text-[12.5px] py-1.5 border-b border-hairsoft"
          >
            <div className="min-w-0 flex-1">
              <div className="text-inksoft truncate">{item.name || "未命名条目"}</div>
              {item.per_day_low !== null && item.per_day_high !== null && (
                <div className="text-[10.5px] text-inkfaint truncate">
                  {`${yuan(item.per_day_low)}–${yuan(item.per_day_high)} / 人·天 × 人数 × 天数`}
                </div>
              )}
            </div>
            <span className="shrink-0 num text-[12.5px] text-ink">
              {item.amount_low !== null && item.amount_high !== null
                ? `${yuan(item.amount_low)}–${yuan(item.amount_high)}`
                : yuan(item.amount_low ?? item.amount_high)}
            </span>
          </li>
        ))}
      </ul>

      {/* 合计 = 区间（下沿保守、上沿含估算）；null 一律"暂无报价"，绝不补 0 */}
      <div className="mt-2 pt-2 border-t border-hairline text-right num text-[13px] text-ink">
        {totalLow !== null && totalHigh !== null
          ? `合计 ${yuan(totalLow)}–${yuan(totalHigh)}`
          : "合计 暂无报价"}
      </div>

      {assumptions.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[10.5px] text-inkfaint">
          {assumptions.map((a, i) => <li key={`${a}-${i}`}>{`假设：${a}`}</li>)}
        </ul>
      )}
      {basis && (
        <div className="mt-1.5 text-[10.5px] text-inkfaint" title={basis}>
          {`依据：${basis}`}
        </div>
      )}
    </CardShell>
  );
}
```

- [ ] **Step 5: 注册卡片与契约镜像**

`web/src/types/models.ts` 的 `BudgetItem`（`source_ref` 之后）加三字段、`BudgetBreakdown`（`uncovered` 之后）加两字段——全部可选，旧快照仍可反序列化：

```ts
  amount_low?: number | null;
  amount_high?: number | null;
  basis?: string;
```

```ts
  total_high?: number | null;
  over_budget_possible?: boolean;
```

`web/src/components/cards/registry.tsx`：

- 顶部 import 加 `import { LivingCostCard } from "./LivingCostCard";`（放在 `ItineraryCard` 之后，字母序）；
- `toolCardRegistry` 聚合能力段加 `estimate_living_cost: LivingCostCard,`；
- `toolVisibility` full 段加 `estimate_living_cost: "full",`；
- 头部注释「14 项」改「15 项」，「8 搜索原语 + 3 聚合 + 3 文档 = 14」改「8 搜索原语 + 4 聚合 + 3 文档 = 15（聚合新增 estimate_living_cost）」。

`BudgetBar` 刻意不动：分段条仍按 `item.amount`（下沿）绘制——上沿是估算上限而非已确认支出，画进「预算累计」会与语义冲突；区间只在 BudgetCard 金额列与合计行呈现。

- [ ] **Step 6: 跑测试确认通过**

Run: `cd web; npx vitest run tests/toolCards.spec.tsx tests/livingCostCard.spec.tsx`
Expected: PASS

- [ ] **Step 7: 类型检查**

Run: `cd web; npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 8: 提交（暂不执行，见 Global Constraints）**

```bash
git add web/src/components/cards/BudgetCard.tsx web/src/components/cards/LivingCostCard.tsx web/src/components/cards/registry.tsx web/src/types/models.ts web/tests/toolCards.spec.tsx web/tests/livingCostCard.spec.tsx
```

---

## Task 6: 今日日期锚点

**Files:**
- Modify: `kernel/guidance/phases.py`（末尾追加 `time_anchor`）
- Modify: `web_host/agent/context.py`（`build_system_prompt` 最前拼 anchor）
- Modify: `core/orchestrator.py`（`_system_prompt` 同序）
- Test: `tests/test_guidance_time_anchor.py`

**Interfaces:**
- Produces: `phases.time_anchor(today: date | None = None) -> str`
- Consumes: 无

- [ ] **Step 1: 写失败测试**

```python
# tests/test_guidance_time_anchor.py
from datetime import date

from fieldwork_agent.kernel.guidance.phases import time_anchor


def test_anchor_states_today_and_weekday():
    text = time_anchor(date(2026, 10, 18))
    assert "今天是 2026-10-18（周日）" in text
    assert "过去日期" in text and "flyai_invalid_date" in text


def test_anchor_defaults_to_real_today():
    text = time_anchor()
    assert text.startswith("今天是 ")
    assert str(date.today()) in text


def test_web_and_cli_prompts_carry_anchor():
    from fieldwork_agent.core.orchestrator import _system_prompt
    from fieldwork_agent.web_host.agent.context import build_system_prompt

    for prompt in (build_system_prompt("research_positioning", "快照"),
                   _system_prompt("research_positioning", "快照")):
        assert "今天是 " in prompt
        assert "出发日期" in prompt
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_guidance_time_anchor.py -v`
Expected: FAIL — `ImportError: cannot import name 'time_anchor'`

- [ ] **Step 3: 实现 time_anchor**

`kernel/guidance/phases.py` 顶部加 `from datetime import date`，文件末尾追加：

```python
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def time_anchor(today: date | None = None) -> str:
    """当前日期锚点（system 最前段）：模型只有知道「今天」才不会给过去日期。"""
    day = today or date.today()
    return (f"今天是 {day.isoformat()}（{_WEEKDAYS[day.weekday()]}）。"
            "行程、机票/火车/酒店搜索一律从今天起算：出发日期与入住/离店日期必须是"
            "今天或之后的日期；计划里的历史日程不能当作实时搜索日期。过去日期会被"
            "拒绝并返回 flyai_invalid_date，请改用未来日期重试并向用户说明。")
```

- [ ] **Step 4: 两处 system 拼装接入**

`web_host/agent/context.py` 的 `build_system_prompt`（Task 3 改后）再改最前：

```python
def build_system_prompt(phase: str, snapshot: str) -> str:
    return "\n\n".join([phases.time_anchor(), phases.phase_prompt(phase),
                        COST_GUIDANCE, _TOOL_GUIDANCE, snapshot])
```

`core/orchestrator.py` 的 `_system_prompt`（Task 3 新建）同序：

```python
def _system_prompt(phase: str, snapshot: str) -> str:
    """CLI 回合 system（与 web_host/agent/context.py 同序拼装）。"""
    return "\n\n".join([phases.time_anchor(), phases.phase_prompt(phase),
                        COST_GUIDANCE, _TOOL_GUIDANCE, snapshot])
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_guidance_time_anchor.py tests/test_guidance.py tests/test_web_agent_loop.py -v`
Expected: PASS

- [ ] **Step 6: 提交（暂不执行，见 Global Constraints）**

```bash
git add kernel/guidance/phases.py web_host/agent/context.py core/orchestrator.py tests/test_guidance_time_anchor.py
```

---

## Task 7: 搜索原语前置日期校验与错误映射

**Files:**
- Modify: `kernel/sources/flyai.py`
- Test: `tests/test_sources_flyai.py`

**Interfaces:**
- Produces: `flyai._today() -> date`（单点可 monkeypatch）、`flyai._future_date_envelope(value, field, today) -> Envelope | None`
- 行为：`search_flights`/`search_trains`/`search_hotels`/`search_marriott_hotels` 在拼 argv 前校验日期；过去日期直接返回 `flyai_invalid_date`，**不启动 CLI**。
- `_error_envelope` 新增「出发日期非法」映射（模型或旧路径仍可能把过去日期送到 CLI）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sources_flyai.py`：

```python
import datetime as dt


def test_past_dep_date_rejected_before_cli(monkeypatch):
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 18))
    called = []
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command", lambda a: called.append(a) or {"status": 0, "data": {}})
    env = client.search_trains("昆明", "大理", dep_date="2026-07-15")
    assert env.ok is False and env.error.code == "flyai_invalid_date"
    assert "2027-07-15" in env.error.hint
    assert "今天（2026-10-18）" in env.error.hint
    assert called == []                                  # 未启动 CLI


def test_today_and_future_dates_pass(monkeypatch):
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 18))
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": 0, "data": {"itemList": []}})
    assert client.search_flights("北京", "大理", dep_date="2026-10-18").ok is True
    assert client.search_trains("昆明", "大理", dep_date="2026-12-01").ok is True


def test_back_date_and_hotel_dates_validated(monkeypatch):
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 18))
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": 0, "data": {"itemList": []}})
    flights = client.search_flights("北京", "大理", dep_date="2026-11-01",
                                    back_date="2026-07-01")
    assert flights.error.code == "flyai_invalid_date" and "返程日期" in flights.error.message
    hotels = client.search_hotels("大理", check_in="2026-11-01", check_out="2026-10-01")
    assert hotels.error.code == "flyai_invalid_date" and "离店日期" in hotels.error.message
    marriott = client.search_marriott_hotels("大理", check_in="2026-07-01")
    assert marriott.error.code == "flyai_invalid_date"


def test_unparseable_date_passes_to_cli(monkeypatch):
    """非 YYYY-MM-DD（如「7月15日」）不做本地判定，交 CLI 报错并走 _error_envelope。"""
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 18))
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": 0, "data": {"itemList": []}})
    assert client.search_trains("昆明", "大理", dep_date="7月15日").ok is True


def test_departure_date_illegal_maps_to_invalid_date(monkeypatch):
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": 1, "message": "出发日期非法"})
    env = client.search_trains("昆明", "大理")
    assert env.ok is False and env.error.code == "flyai_invalid_date"
    assert "今天" in env.error.hint or "未来" in env.error.hint
```

另外，`test_search_flights_argv_full_order`（使用 `dep_date="2026-11-01"`）会随真实日期过期，改为固定今天：

```python
def test_search_flights_argv_full_order(monkeypatch):
    """可选参数按旧 tools/flyai_tools.py 的顺序拼装，空值不出现。"""
    import datetime as dt
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 18))
    calls = {}
    def fake_run(args):
        calls["args"] = args
        return {"status": 0, "data": {"itemList": []}}
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command", fake_run)
    env = client.search_flights("北京", "大理", dep_date="2026-11-01", max_price="1500")
    assert env.ok and env.data.total == 0
    assert calls["args"] == ["search-flight", "--origin", "北京", "--destination", "大理",
                             "--dep-date", "2026-11-01", "--sort-type", "2",
                             "--max-price", "1500"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_sources_flyai.py -v`
Expected: FAIL — `AttributeError: module 'flyai' has no attribute '_today'`

- [ ] **Step 3: 实现校验与映射**

`kernel/sources/flyai.py`：`from datetime import date, datetime`，在 `_error_envelope` 之前加：

```python
def _today() -> date:
    """当前日期（单点可 monkeypatch：测试不依赖真实墙钟）。"""
    return date.today()


def _future_date_envelope(value: str, field: str, today: date) -> Envelope | None:
    """过去日期 → flyai_invalid_date 包络（不启动 CLI）；空/无法解析/未来 → None。"""
    if not value:
        return None
    try:
        day = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None                       # 非 ISO 日期交 CLI 判定，避免自造格式规则
    if day >= today:
        return None
    try:
        suggested = day.replace(year=day.year + 1)
    except ValueError:                    # 2/29 → 次年无此日
        suggested = day.replace(year=day.year + 1, day=28)
    while suggested < today:
        suggested = suggested.replace(year=suggested.year + 1)
    return Envelope(ok=False, error=ToolError(
        code="flyai_invalid_date",
        message=f"{field}非法：{value} 早于今天（{today.isoformat()}）",
        hint=(f"实时搜索仅支持今天（{today.isoformat()}）及以后的日期；"
              f"若计划 {day.month} 月 {day.day} 日出发，请改用 {suggested.isoformat()} 重试")))
```

`_error_envelope` 的「入离时间校验失败」分支之后加：

```python
    elif "出发日期非法" in message or "invalid date" in low or "date_invalid" in low:
        code, hint = ("flyai_invalid_date",
                      "出发日期非法：实时搜索仅支持今天及以后的日期；"
                      "请把计划中的历史日程换成一个未来的可比日期后重试，"
                      "并向用户说明价格仅为未来参照")
```

四个搜索方法在拼 argv 前插入校验（以 `search_trains` 为例，其余同构）：

```python
        today = _today()
        for value, field in ((dep_date, "出发日期"), (back_date, "返程日期")):
            bad = _future_date_envelope(value, field, today)
            if bad is not None:
                return bad
```

- `search_flights`：`dep_date`/`back_date`
- `search_trains`：`dep_date`/`back_date`
- `search_hotels`：`check_in`（入住日期）/`check_out`（离店日期）
- `search_marriott_hotels`：`check_in`/`check_out`

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_sources_flyai.py -v`
Expected: PASS

- [ ] **Step 5: 提交（暂不执行，见 Global Constraints）**

```bash
git add kernel/sources/flyai.py tests/test_sources_flyai.py
```

---

## Task 8: 前导语识别与回合中断标记

**Files:**
- Modify: `web_host/agent/loop.py:105-166`、`:270-328`、`:368-387`
- Test: `tests/test_web_agent_loop.py`（改 2 条 + 新增 2 条）

**Interfaces:**
- Produces: `loop._looks_like_preamble(text: str) -> bool`；`turn_done.incomplete: bool`
- 行为：无 `tool_calls` 且内容像前导语（结尾冒号/逗号等，或 ≤120 字且含承诺词）→ **带工具**重问一次；`finish_reason == "length"` 不走此分支；连续两次前导语则交收敛块，收敛仍无正文时 `incomplete=True`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_web_agent_loop.py`：

```python
def test_preamble_and_truncation_rules():
    from fieldwork_agent.web_host.agent.loop import _looks_like_preamble, _looks_truncated

    assert _looks_like_preamble("先问最实际的：")                       # 结尾冒号
    assert _looks_like_preamble("我这就把信息写入项目状态")              # 承诺词 + 无句号
    assert not _looks_like_preamble("这是完整回答。")
    assert not _looks_like_preamble("好的。")
    # 冒号不再算「截断」（前导语走带工具重问，而非 tools=None 续写）
    assert _looks_truncated("先问最实际的：", "stop") is False
    assert _looks_truncated("价格约 ¥65×", "stop") is True
    assert _looks_truncated("完整回答。", "length") is True


def test_preamble_twice_falls_back_and_marks_incomplete(env):
    """连续两次前导语：不再重问，交收敛块；收敛仍无正文 → turn_done.incomplete=True。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "我这就去查："},
         {"content": "我这就去查：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],
        [{"delta": "马上开始："},
         {"content": "马上开始：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],
        [{"content": "", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    done = next(e for e in tm.replay("p1", turn.turn_id, after_seq=0)
                if e.event == "turn_done")
    assert done.data["incomplete"] is True
    assert len(llm.calls) == 3                    # 主循环 2 次 + 收敛 1 次
```

`test_half_sentence_stop_triggers_continuation`（1037-1056 行）整段改为：

```python
def test_colon_preamble_retries_with_tools(env):
    """生产实录场景：finish_reason=stop 且回复停在「先问最实际的：」——
    这是前导语而非最终回答：带工具重问一次（不同于 length 的 tools=None 续写），
    两轮文本拼接为完整回复。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "先问最实际的："},
         {"content": "先问最实际的：", "tool_calls": None,
          "finish_reason": "stop", "usage": {"total_tokens": 223}}],
        [{"delta": "经费来源是自费还是课题经费？"},
         {"content": "经费来源是自费还是课题经费？", "tool_calls": None,
          "finish_reason": "stop", "usage": {"total_tokens": 8}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert deltas == "先问最实际的：经费来源是自费还是课题经费？"
    assert len(llm.calls) == 2
    retry_messages, retry_kw = llm.calls[1]
    assert retry_kw.get("tools") is not None            # 重问必须带工具
    assert retry_messages[-1]["role"] == "system"
    assert "前导语" in retry_messages[-1]["content"]
    assistant = next(m for m in repo.get_messages("p1") if m["role"] == "assistant")
    assert assistant["content"] == "先问最实际的：经费来源是自费还是课题经费？"
```

`test_turn_done_and_meta_carry_finish_reason`（992-1005 行）末尾加一行，钉住正常回合不标中断：

```python
    assert done.data["incomplete"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_web_agent_loop.py -k "preamble or colon or finish_reason" -v`
Expected: FAIL — `ImportError: cannot import name '_looks_like_preamble'`

- [ ] **Step 3: 实现前导语判定与重问**

`web_host/agent/loop.py` 主循环初始化区（第 105-109 行附近）加三个状态：

```python
    accumulated_reply = ""
    stream_prefix = ""          # 前导语文本（已流出，重问后仍拼回持久化正文）
    preamble_retried = False    # 只重问一次，防无限循环
    incomplete = False          # 回合未收敛出正文 → turn_done.incomplete
    has_final_answer = False
```

第 157-166 行整段替换为：

```python
            raw = final_resp.get("content", "") or ""
            if raw:
                accumulated_reply = stream_prefix + raw
            usage_total = final_resp.get("usage", {}) or usage_total
            tool_calls = final_resp.get("tool_calls")
            if not tool_calls:
                finish_reason = str(final_resp.get("finish_reason") or "")
                answer = raw.strip()
                if (answer and finish_reason != "length" and not preamble_retried
                        and _looks_like_preamble(raw)):
                    # 前导语（"我这就去查…"/"先问最实际的："）不是最终回答：文本
                    # 已流出，记入前缀；带工具重问一次让它真正动手。length 不走此
                    # 分支（截断续写仍然 tools=None，保持既有语义）。
                    preamble_retried = True
                    stream_prefix += raw
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "system", "content": _PREAMBLE_NUDGE})
                    continue
                if answer and (finish_reason == "length" or not _looks_like_preamble(raw)):
                    has_final_answer = True
                break
```

收敛块成功分支（第 310-312 行）的赋值改为保留前缀：

```python
                if (final_resp.get("content") or "").strip():
                    accumulated_reply = stream_prefix + final_resp["content"]
                    has_final_answer = True
```

收敛块两个失败分支各在首行加 `incomplete = True`（`fallback`/`note` 文案与 `message_delta` 发射保持原样）：

```python
        if not accumulated_reply:
            incomplete = True
```

```python
        elif not has_final_answer:
            incomplete = True
```

`turn_done` payload（第 368-372 行）加一键：

```python
        tm.append_event(pid, turn_id, "turn_done",
                        {"reply_id": turn_id, "usage": usage_total,
                         "snapshot_ids": snapshot_ids,
                         "quick_replies": quick_replies,
                         "finish_reason": finish_reason,
                         "incomplete": incomplete})
```

文件末尾的常量与函数（第 376-386 行）替换为：

```python
# 未完标点/开引号/截断算式：非空正文以其结尾几乎必为句中截断（服务端异常终止），
# 触发一次续写兜底；句号/问号/叹号/引号闭合等完整结尾不在此列。
# 冒号移出本表：以冒号结尾多为「前导语」，走带工具重问（见 _looks_like_preamble）。
_INCOMPLETE_TAIL_CHARS = '，,、；;—…"“‘\'×÷≈+-='

# 前导语尾字符 + 承诺词：无 tool_calls 且命中任一 → 不是最终回答
_PREAMBLE_TAIL_CHARS = "：:，,、；;—…"
_PROMISE_MARKERS = ("我这就", "马上", "我先", "接下来", "下面", "让我", "这就去",
                    "先来", "我来", "实测", "查一下", "查一查")

_PREAMBLE_NUDGE = ("你上一条回复只是前导语（说明接下来要做什么），尚未给出结果。"
                   "请立即调用合适的工具获取真实数据，拿到结果后再给出完整回答；"
                   "不要再输出「我这就去查」这类前导语。")


def _looks_like_preamble(text: str) -> bool:
    """前导语判定：以未完标点收尾，或短句且含承诺词、但未以句末标点收尾。"""
    body = text.strip()
    if not body:
        return False
    if body[-1] in _PREAMBLE_TAIL_CHARS:
        return True
    return (len(body) <= 120 and body[-1] not in "。！？"
            and any(marker in body for marker in _PROMISE_MARKERS))


def _looks_truncated(reply: str, finish_reason: str) -> bool:
    """终止帧是否截断：length 触顶，或正文尾部落在未完标点/截断算式上。"""
    if finish_reason == "length":
        return True
    tail = reply.rstrip()
    return bool(tail) and tail[-1] in _INCOMPLETE_TAIL_CHARS
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_web_agent_loop.py -v`
Expected: PASS（含既有 length 续写用例不变）

- [ ] **Step 5: 提交（暂不执行，见 Global Constraints）**

```bash
git add web_host/agent/loop.py tests/test_web_agent_loop.py
```

---

## Task 9: 仓库层 pop_last_message

**Files:**
- Modify: `kernel/repository/protocol.py:68-76`
- Modify: `kernel/repository/json_repo.py:306-321`
- Modify: `kernel/repository/sqlite_repo.py:451-479`
- Test: `tests/repository_contract.py`（契约，双实现）
- Test: `tests/test_json_repo.py`（窗口同步）

**Interfaces:**
- Produces: `ProjectRepository.pop_last_message(pid: str, role: str | None = None) -> dict | None`
- 语义：弹出最近一条（可按 role 过滤）并返回该条；无匹配返回 None；未知项目 `KeyError`。

- [ ] **Step 1: 写失败测试**

`tests/repository_contract.py` 的「消息（单轨制）」段落末尾追加：

```python
    def test_pop_last_message_removes_tail(self, repo):
        repo.append_message("p1", "user", "提问")
        repo.append_message("p1", "assistant", "回答")
        popped = repo.pop_last_message("p1")
        assert popped["role"] == "assistant" and popped["content"] == "回答"
        assert [m["content"] for m in repo.get_messages("p1", limit=0)] == ["提问"]

    def test_pop_last_message_role_filter(self, repo):
        repo.append_message("p1", "user", "问")
        repo.append_message("p1", "assistant", "答")
        assert repo.pop_last_message("p1", role="user")["content"] == "问"
        assert [m["content"] for m in repo.get_messages("p1", limit=0)] == ["答"]

    def test_pop_last_message_none_when_no_match(self, repo):
        assert repo.pop_last_message("p1") is None
        repo.append_message("p1", "user", "问")
        assert repo.pop_last_message("p1", role="assistant") is None
        assert [m["content"] for m in repo.get_messages("p1", limit=0)] == ["问"]

    def test_pop_last_message_unknown_project(self, repo):
        with pytest.raises(KeyError):
            repo.pop_last_message("ghost")
```

`tests/test_json_repo.py` 追加（窗口同步：state 内 `dialogue_context` 也要弹）：

```python
def test_pop_last_message_syncs_dialogue_window(json_repo):
    json_repo.append_message("p1", "user", "提问")
    json_repo.append_message("p1", "assistant", "回答")
    assert [m["role"] for m in _read_state(json_repo, "p1")["dialogue_context"]] == [
        "user", "assistant"]
    json_repo.pop_last_message("p1")
    state = _read_state(json_repo, "p1")
    assert [m["role"] for m in state["dialogue_context"]] == ["user"]
    assert [m["content"] for m in json_repo.get_messages("p1", limit=0)] == ["提问"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_json_repo.py tests/test_sqlite_repo.py -k pop_last -v`
Expected: FAIL — `AttributeError: 'JsonRepository' object has no attribute 'pop_last_message'`

- [ ] **Step 3: 协议方法**

`kernel/repository/protocol.py` 的 `get_messages` 之后：

```python
    def pop_last_message(self, pid: str, role: str | None = None) -> dict | None:
        """弹出最近一条消息（可按 role 过滤）并返回该条；无匹配返回 None。

        用于「重新生成」：先弹 assistant 再弹 user，随后以同一文本新建回合。
        """
        ...
```

- [ ] **Step 4: JsonRepository 实现**

`_write_chat_lines` 之后加原子重写，`get_messages` 之后加弹出：

```python
    def _rewrite_chat_lines(self, pid: str, entries: list[dict]) -> None:
        """整表重写消息史（弹末条用）：先写 tmp 再 os.replace，避免半行。"""
        path = self._chat_file(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / (path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
```

```python
    def pop_last_message(self, pid: str, role: str | None = None) -> dict | None:
        """弹出最近一条（可按 role 过滤）：jsonl 整表重写 + state 窗口同步弹出。"""
        state = self._load(pid)
        self._backfill_chat_log(pid, state)
        entries = self._read_chat_log(pid)
        index = next((i for i in range(len(entries) - 1, -1, -1)
                      if role is None or entries[i].get("role") == role), None)
        if index is None:
            return None
        popped = entries.pop(index)
        self._rewrite_chat_lines(pid, entries)
        window = state["dialogue_context"]
        key = (popped.get("ts"), popped.get("role"), popped.get("content"))
        for i in range(len(window) - 1, -1, -1):
            w = window[i]
            if (w.get("ts"), w.get("role"), w.get("content")) == key:
                del window[i]
                break
        self._save(pid, state)
        return popped
```

- [ ] **Step 5: SqliteRepository 实现**

`get_messages` 之后：

```python
    def pop_last_message(self, pid: str, role: str | None = None) -> dict | None:
        """弹出最近一条（可按 role 过滤）：按 id 倒序取一条后 DELETE 并推进 updated_at。"""
        self._require(pid)
        sql = "SELECT id, role, content, meta, ts FROM messages WHERE project_id=?"
        params: list = [pid]
        if role is not None:
            sql += " AND role=?"
            params.append(role)
        sql += " ORDER BY id DESC LIMIT 1"
        now = _now()
        with self._connect(immediate=True) as conn:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM messages WHERE id=?", (row["id"],))
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?", (now, pid))
        return {"role": row["role"], "content": row["content"], "ts": row["ts"],
                "metadata": json.loads(row["meta"])}
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_json_repo.py tests/test_sqlite_repo.py -v`
Expected: PASS（契约用例在两种实现上各跑一遍）

- [ ] **Step 7: 提交（暂不执行，见 Global Constraints）**

```bash
git add kernel/repository/protocol.py kernel/repository/json_repo.py kernel/repository/sqlite_repo.py tests/repository_contract.py tests/test_json_repo.py
```

---

## Task 10: 重新生成路由

**Files:**
- Modify: `web_host/api/turns.py`（文件末尾新增 `retry_turn`）
- Test: `tests/test_web_turns_api.py`

**Interfaces:**
- Produces: `POST /projects/{project_id}/turns/{turn_id}/retry` → 201 `{turn_id, status}`
- Consumes: `repo.pop_last_message`（Task 9）、`get_turn_manager`、`_resolve_clients`、`_execute_turn`
- 校验顺序（任一不满足都不改历史）：回合存在（404）→ 终态（409）→ 无写锁（409）→ 末尾两条必须 `[user, assistant]`（409 `retry_not_applicable`）→ 解析客户端（403）→ 弹出两条 → 新建回合。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_web_turns_api.py`：

```python
async def test_retry_regenerates_last_turn(app, client):
    r = await client.post("/projects/p1/messages", json={"text": "帮我排行程"})
    first = r.json()["turn_id"]
    await asyncio.sleep(0.5)                     # 等首回合完成（assistant 落盘）

    rr = await client.post(f"/projects/p1/turns/{first}/retry")
    assert rr.status_code == 201
    assert rr.json()["turn_id"] != first
    await asyncio.sleep(0.5)                     # 等重生成完成

    msgs = (await client.get("/projects/p1/messages")).json()
    assert [m["content"] for m in msgs] == ["帮我排行程", "收到"]   # 不重复、不丢


async def test_retry_running_turn_rejected(app, client):
    from fieldwork_agent.web_host.deps import get_tm_raw
    handle = get_tm_raw(app).create_turn("p1")    # 只建状态不执行 → 非终态
    r = await client.post(f"/projects/p1/turns/{handle.turn_id}/retry")
    assert r.status_code == 409


async def test_retry_unknown_turn_404(client):
    r = await client.post("/projects/p1/turns/decafbad01234567/retry")
    assert r.status_code == 404


async def test_retry_requires_user_and_assistant_tail(app, client):
    from fieldwork_agent.web_host.deps import get_repo_raw
    r = await client.post("/projects/p1/messages", json={"text": "hi"})
    tid = r.json()["turn_id"]
    await asyncio.sleep(0.5)
    get_repo_raw(app).append_message("p1", "user", "末条不是 assistant")
    rr = await client.post(f"/projects/p1/turns/{tid}/retry")
    assert rr.status_code == 409
    assert "retry_not_applicable" in rr.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_web_turns_api.py -k retry -v`
Expected: FAIL — 405（路由不存在）

- [ ] **Step 3: 实现路由**

`web_host/api/turns.py` 末尾（`cancel_turn` 之后）追加：

```python
@router.post("/projects/{project_id}/turns/{turn_id}/retry", status_code=201)
async def retry_turn(project_id: str, turn_id: str, request: Request,
                     background: BackgroundTasks,
                     owner: str | None = Depends(owner_from_request)):
    """重新生成：弹出末条 assistant 与 user，以同一文本新建回合。

    只允许终态回合且对话末尾必须是 [user, assistant]——先校验后弹出，
    任何一步失败都不改历史（否则弹完发现不可重试就丢消息）。
    """
    repo = get_repo(request)
    checked_owned_project(project_id, repo, owner)
    checked_turn_id(turn_id)
    tm = get_turn_manager(request)
    handle = tm.get_turn(project_id, turn_id)
    if handle is None:
        raise HTTPException(404, "回合不存在")
    if handle.status not in TERMINAL_STATUSES:
        raise HTTPException(409, "该回合仍在执行，无法重新生成")
    if tm.is_writing(project_id):
        raise HTTPException(409, "该项目有正在执行的回合")
    tail = repo.get_messages(project_id, limit=2)
    if len(tail) != 2 or tail[0]["role"] != "user" or tail[1]["role"] != "assistant":
        raise HTTPException(409, detail={
            "code": "retry_not_applicable",
            "message": "对话末尾不是「用户消息 + 助手回复」，无法重新生成",
        })
    llm, flyai, amap = _resolve_clients(request)
    repo.pop_last_message(project_id, role="assistant")
    text = repo.pop_last_message(project_id, role="user")["content"]
    turn = tm.create_turn(project_id)
    background.add_task(_execute_turn, request.app, project_id,
                        turn.turn_id, text, llm, flyai, amap)
    return {"turn_id": turn.turn_id, "status": turn.status}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_web_turns_api.py -v`
Expected: PASS

- [ ] **Step 5: 提交（暂不执行，见 Global Constraints）**

```bash
git add web_host/api/turns.py tests/test_web_turns_api.py
```

---

## Task 11: 前端「重新生成」

**Files:**
- Modify: `web/src/api/rest.ts:117-118`
- Modify: `web/src/types/events.ts:49-56`
- Modify: `web/src/stores/conversation.ts`
- Modify: `web/src/hooks/useTurn.ts`
- Modify: `web/src/components/chat/Timeline.tsx:59-75`、`:107-109`
- Modify: `web/src/App.tsx:249`
- Test: `web/tests/useTurn.spec.tsx`、`web/tests/chat.spec.tsx`

**Interfaces:**
- Consumes: `POST /turns/{id}/retry`（Task 10）、`turn_done.incomplete`（Task 8）
- Produces: `rest.retryTurn(id, turnId)`；`useConversationStore.dropLastAssistant()`；`useTurn().retry / canRetry / incomplete`；`Timeline` 的 `onRetry/canRetry/retrying/incomplete` props。

- [ ] **Step 1: 写失败测试**

`web/src/types/events.ts` 的 `TurnDone` 已计划加 `incomplete?: boolean`（Step 3）；测试直接按新形状写。

追加到 `web/tests/useTurn.spec.tsx`：

```tsx
describe("useTurn — 重新生成", () => {
  test("retry 成功：POST /turns/t1/retry → 丢末条 assistant → 订阅新回合", async () => {
    useConversationStore.setState({
      messages: [{ role: "user", content: "帮我排行程" },
                 { role: "assistant", content: "残缺回复" }],
      streamBuffer: "", turnRunning: false, lastUsage: {},
    });
    const urls: string[] = [];
    // 已结束且中断的旧回合：turn_done 置 retryable，incomplete 置中断提示
    const sseOne = sseResp([
      'id: 1\nevent: turn_done\n' +
        'data: {"turn_id":"t1","seq":1,"reply_id":"t1","usage":{},"incomplete":true}\n\n',
    ]);
    const sseTwo = sseResp([
      'id: 1\nevent: turn_done\n' +
        'data: {"turn_id":"t2","seq":1,"reply_id":"t2","usage":{},"incomplete":false}\n\n',
    ]);
    const fetchMock = vi.fn(async (url: string) => {
      const u = String(url);
      urls.push(u);
      if (u.endsWith("/retry")) return jsonResp({ turn_id: "t2", status: "running" }, true, 201);
      // adopt 的 re-hydrate：返回当前 store 史（adopt 的 hydrate 是 fire-and-forget，
      // 固定返回 [] 会把断言打空）
      if (u.endsWith("/messages")) return jsonResp(useConversationStore.getState().messages, true, 200);
      if (u.includes("/turns/t2/events")) return sseTwo.resp;
      return sseOne.resp;
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.adopt("t1"); });
    expect(result.current.canRetry).toBe(true);

    await act(async () => { await result.current.retry(); });
    expect(urls.some((u) => u.endsWith("/turns/t1/retry"))).toBe(true);
    expect(urls.some((u) => u.includes("/turns/t2/events"))).toBe(true);
    // 残缺 assistant 已丢弃；新回合无 delta → 仅剩 user 消息
    expect(useConversationStore.getState().messages.map((m) => m.content))
      .toEqual(["帮我排行程"]);
    expect(sseTwo.cancel).toHaveBeenCalled();
  });

  test("retry 被拒（409）→ 如实上屏，不动本地消息", async () => {
    useConversationStore.setState({
      messages: [{ role: "user", content: "问" },
                 { role: "assistant", content: "答" }],
      streamBuffer: "", turnRunning: false, lastUsage: {},
    });
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResp({ detail: "对话末尾不是「用户消息 + 助手回复」" }, false, 409)));

    const { result } = renderHook(() => useTurn("p1"));
    await act(async () => { await result.current.retry("t1"); });
    expect(useConversationStore.getState().messages.map((m) => m.content))
      .toEqual(["问", "答"]);
    expect(useUiStore.getState().degradedBanner?.code).toBe("retry_failed");
  });
});
```

追加到 `web/tests/chat.spec.tsx`（顶部 import 补 `vi`）：

```tsx
describe("Timeline — 重新生成", () => {
  test("中断提示 + 按钮点击回调", () => {
    const onRetry = vi.fn();
    render(<Timeline onRetry={onRetry} canRetry incomplete />);
    expect(screen.getByText(/本轮生成中断/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  test("无中断无按钮", () => {
    render(<Timeline />);
    expect(screen.queryByRole("button", { name: "重新生成" })).not.toBeInTheDocument();
    expect(screen.queryByText(/本轮生成中断/)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web; npx vitest run tests/useTurn.spec.tsx tests/chat.spec.tsx`
Expected: FAIL — `rest.retryTurn` 不存在 / `Timeline` 不认 `onRetry`

- [ ] **Step 3: 类型与 API**

`web/src/types/events.ts` 的 `TurnDone`（`quick_replies` 之后）加：

```ts
  /** 回合未收敛出正文（前导语/触顶/收敛失败）→ 前端展示中断提示与重新生成。 */
  incomplete?: boolean;
```

`web/src/api/rest.ts` 的 `cancelTurn` 之后加：

```ts
  // 重新生成：后端弹出末条 assistant + user，以同一文本新建回合（201）
  retryTurn: (id: string, turnId: string) =>
    req<CreateTurnResponse>(`/projects/${id}/turns/${turnId}/retry`, { method: "POST" }),
```

- [ ] **Step 4: store 丢弃末条 assistant**

`web/src/stores/conversation.ts` 接口加：

```ts
  dropLastAssistant: () => void;         // 重新生成：后端已弹出，本地同步丢弃
```

实现（`cancelTurn` 之后）：

```ts
  // 重新生成前调用：后端已把末条 assistant 弹出并重放 user 消息，本地只丢这一条；
  // quickReplies 一并清空（旧选项属于已作废的回复）。
  dropLastAssistant: () => set((st) => {
    const msgs = st.messages.slice();
    if (msgs.length && msgs[msgs.length - 1].role === "assistant") msgs.pop();
    return { messages: msgs, quickReplies: [] };
  }),
```

- [ ] **Step 5: useTurn 接入 retry**

1）import 改为 `import { rest, ApiError, isAppTokenError, apiDetail } from "../api/rest";`

2）状态（`conflict` 之后）：

```ts
  const [lastTurnId, setLastTurnId] = useState<string | null>(null);
  const [retryable, setRetryable] = useState(false);
  const [incomplete, setIncomplete] = useState(false);
```

3）`send` 成功拿到 turnId 后（`turnIdRef.current = turnId;` 处）加：

```ts
    setLastTurnId(turnId);
    setRetryable(false);
    setIncomplete(false);
```

`send` 的 `onEvent` 回调内（`dispatchEvent` 之后）加：

```ts
          if (env.event === "turn_done") {
            setRetryable(true);
            setIncomplete(env.data.incomplete === true);
          }
```

4）`adopt` 同样的三处：`turnIdRef.current = turnId;` 后置 `setLastTurnId(turnId); setRetryable(false); setIncomplete(false);`；`onEvent` 里同样在 `turn_done` 时置 `setRetryable(true)` 与 `setIncomplete(...)`。

5）`cancel` 里加：

```ts
    setRetryable(false);
    setLastTurnId(null);
    setIncomplete(false);
```

6）`retry`（`cancel` 之后、`return` 之前）：

```ts
  // 重新生成：只对「本会话刚结束的回合」开放（lastTurnId 由 send/adopt 写入）；
  // 必须先等 POST 成功再丢本地 assistant——失败时历史保持原样。
  const retry = useCallback(async (turnId?: string) => {
    const target = turnId ?? lastTurnId;
    if (!target || busy) return;
    setKeyMissing(false);
    setRetryable(false);
    try {
      const created = await rest.retryTurn(projectId, target);
      useConversationStore.getState().dropLastAssistant();
      useConversationStore.getState().beginTurn();
      setIncomplete(false);
      await adopt(created.turn_id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setKeyMissing(true);
      else if (isAppTokenError(err)) {
        useUiStore.getState().applyEvent({
          event: "error", code: "send_failed",
          message: "访问令牌缺失或已失效，重新生成被拒绝",
          hint: "请在「密钥与登录」弹层填写访问令牌并保存",
        });
        onAuthErrorRef.current?.();
      } else {
        const detail = err instanceof ApiError ? apiDetail(err) : null;
        useUiStore.getState().applyEvent({
          event: "error", code: "retry_failed",
          message: detail ?? (err instanceof ApiError && err.status === 409
            ? "无法重新生成：对话末尾不是「用户消息 + 助手回复」"
            : String(err)),
          hint: "请等当前回合结束，或直接重新发送这条问题",
        });
      }
    }
  }, [projectId, busy, lastTurnId, adopt]);
```

7）返回值改为：

```ts
  return {
    send, adopt, cancel, retry, busy, conflict, keyMissing,
    canRetry: retryable && lastTurnId !== null && !busy,
    incomplete,
  };
```

- [ ] **Step 6: Timeline 渲染中断提示与按钮**

`web/src/components/chat/Timeline.tsx` 签名（第 59 行）改为：

```tsx
export function Timeline({
  onRetry,
  canRetry = false,
  retrying = false,
  incomplete = false,
}: {
  onRetry?: () => void;
  canRetry?: boolean;
  retrying?: boolean;
  incomplete?: boolean;
} = {}) {
```

工具卡块之后（`{visible.length > 0 && ...}` 之后）加：

```tsx
      {/* 回合中断：正文残缺/未收敛 → 给「重新生成」而不让用户自己重写问题 */}
      {(incomplete || canRetry) && (
        <div className="pl-9 flex items-center gap-2 flex-wrap text-[11.5px]">
          {incomplete && (
            <span className="text-ochre">本轮生成中断：正文可能在句中被截断，或未给出结论。</span>
          )}
          {canRetry && onRetry && (
            <button
              type="button"
              onClick={onRetry}
              disabled={retrying}
              className="chip hairline bg-card text-inksoft hover:bg-cardsunk hover:text-ink transition disabled:opacity-50"
            >
              {retrying ? "正在重新生成…" : "重新生成"}
            </button>
          )}
        </div>
      )}
```

- [ ] **Step 7: App 注入**

`web/src/App.tsx:249` 改为：

```tsx
          <Timeline
            onRetry={() => { void turn.retry(); }}
            canRetry={turn.canRetry}
            retrying={turn.busy}
            incomplete={turn.incomplete}
          />
```

- [ ] **Step 8: 跑测试与类型检查**

Run: `cd web; npx vitest run tests/useTurn.spec.tsx tests/chat.spec.tsx; npx tsc --noEmit`
Expected: PASS

- [ ] **Step 9: 提交（暂不执行，见 Global Constraints）**

```bash
git add web/src/api/rest.ts web/src/types/events.ts web/src/stores/conversation.ts web/src/hooks/useTurn.ts web/src/components/chat/Timeline.tsx web/src/App.tsx web/tests/useTurn.spec.tsx web/tests/chat.spec.tsx
```

---

## 最终验收（全部任务完成后执行）

- [ ] **后端全量**：`python -m pytest` → 全绿
- [ ] **静态检查**：`ruff check .` → 本计划改动文件 0 告警。
  - 基线情况（merge `789d5c6` 后实测）：`pytest` 全绿（3 条 symlink skip）、`vitest` 27 文件/308 用例通过、`tsc --noEmit` 0 错误；
  - `ruff check .` 基线已有 6 条与本计划无关的 F04 遗留告警：`kernel/guidance/brief.py` I001/F401、`runtime/state_tools.py` UP037/F821、`tests/test_brief.py` F541、`tests/test_doc_args.py` I001。修不修由用户在最终提交前决定；`.audit/` 是未跟踪草稿目录，不进 CI。
- [ ] **前端全量**：`cd web; npx tsc --noEmit; npm test` → 全绿
- [ ] **手工回放两条生产症状**：
  1. `estimate_living_cost(settlement_type="village", board_mode="self_cook", days=60)` → 餐饮 `¥720–¥1200`、市内交通 `¥0–¥300`，口径行含「村内自炊 · 1 人 × 60 天」与「14.7」；
  2. `search_trains(dep_date="2026-07-15")` → `flyai_invalid_date` 且 hint 指向 `2027-07-15`；对话流不再出现无工具的「我这就去查」悬空句。

## 最终提交（用户确认后执行一次）

Global Constraints 已定：本阶段不逐任务提交。全部实现与验收通过后，把本计划涉及的全部文件合并为一次提交（spec 与计划文档一并入同一个提交）：

```bash
git add docs/superpowers/specs/2026-10-18-living-cost-and-turn-resilience-design.md \
        docs/superpowers/plans/2026-10-18-living-cost-and-turn-resilience.md \
        kernel/aggregation/living_cost.py kernel/aggregation/itinerary.py kernel/models.py \
        kernel/__init__.py kernel/guidance/costs.py kernel/guidance/phases.py \
        kernel/sources/flyai.py kernel/repository/protocol.py kernel/repository/json_repo.py \
        kernel/repository/sqlite_repo.py core/orchestrator.py \
        web_host/agent/context.py web_host/agent/loop.py web_host/api/turns.py \
        web/src/api/rest.ts web/src/types/events.ts web/src/types/models.ts \
        web/src/stores/conversation.ts web/src/hooks/useTurn.ts \
        web/src/components/chat/Timeline.tsx web/src/App.tsx \
        web/src/components/cards/types.ts web/src/components/cards/data.ts \
        web/src/components/cards/BudgetCard.tsx web/src/components/cards/LivingCostCard.tsx \
        web/src/components/cards/SearchResultCard.tsx web/src/components/cards/registry.tsx \
        tests/test_living_cost.py tests/test_budget_contract.py tests/test_guidance_cost.py \
        tests/test_guidance_time_anchor.py tests/test_sources_flyai.py tests/test_itinerary.py \
        tests/test_web_agent_loop.py tests/test_web_turns_api.py tests/test_json_repo.py \
        tests/repository_contract.py \
        web/tests/toolCards.spec.tsx web/tests/livingCostCard.spec.tsx \
        web/tests/useTurn.spec.tsx web/tests/chat.spec.tsx
git commit -m "feat: 生活成本表值区间与回合健壮性（日期校验/前导语重问/重新生成）"
```

