# 生活成本口径与回合健壮性设计

- 日期：2026-10-18
- 触发：生产会话 `field_20261018_202833`（云南鹤庆新华村银器传承，单人驻村两个月，预算 ¥3000）
- 范围：内核（`kernel/aggregation`、`kernel/models.py`、`kernel/sources/flyai.py`、`kernel/guidance`）、Web 宿主（`web_host/agent`、`web_host/api`、`web_host/agent/loop.py`）、CLI 编排（`core/orchestrator.py`）、前端（预算卡、搜索失败卡、对话末尾操作）
- 状态：待用户确认

## 1. 问题与证据

### 1.1 生活费估算脱离现实

会话 turn `ade73dd8` 的流式原文（21:45）：

> 餐饮生活：按 **¥100–150/天** × 60天 ≈ **¥6000–9000** … 合计大致 **¥1万上下**

用户反问后，模型在下一轮自行改口为「按 ¥25–30/天 ≈ ¥1500–1800」——同一会话对同一口径给出相差 4 倍的数字，说明估算没有任何可核验依据。

代码侧证据：

1. `kernel/aggregation/itinerary.py` 只有一条混合口径 `MEALS_PER_PERSON_DAY_DEFAULT = 150.0`（注释写作「市内交通与餐饮估算的人均日标准」），同时表达交通与餐饮，且无聚落类型、无就餐方式维度。
2. 该默认值以 `"default": 150.0` 出现在 `compose_itinerary` 的工具 schema 中（`meals_local_per_person_day`），模型可直读，构成数值锚点；`profile_field_site` 的 `budget_per_night: 200` 同理。
3. 该会话全程未调用 `compose_itinerary`（工具只有 `update_research_context` 与 `search_trains`），预算数字全部来自正文自由发挥。

现实基线（用于表值与 `basis` 文案）：

- 云南农村居民人均食品烟酒支出 5,351 元/年 ≈ **14.7 元/人·天**（自炊口径）。
- 云南农村居民人均消费支出 16,214 元/年 ≈ **44.4 元/人·天**（含居住、交通等全部）。

结论：驻村自炊/搭伙的合理区间是 ¥12–30/人·天；¥100–150 是城市差旅/游客口径，量级错误 4–8 倍。

### 1.2 火车票「出发日期非法」与回合卡死

三段独立缺陷叠加：

1. **system prompt 无「今天」**。`web_host/agent/context.py::build_system_prompt` 与 `core/orchestrator.py` 第 246 行都只拼 `phase_prompt + _TOOL_GUIDANCE + snapshot`。生产当日为 2026-10-18，模型把「暑假 7 月中旬」解析为 `2026-07-15`（已过去 3 个月）。
2. **错误未被识别**。`kernel/sources/flyai.py::_error_envelope` 只把「入离时间校验失败」/`cico_check_param_error` 映射为 `flyai_invalid_date`；飞猪实际返回的「出发日期非法」落入兜底 `flyai_error` + 泛化 hint「可更换关键词或放宽条件后重试」，模型据此写成「日期参数被拒，可能是排期窗口限制」并放弃。本地用同一 CLI 实测：`--dep-date 2027-07-15` 正常返回车次，规则只是「不得早于今天」。
3. **悬空前导语被当作最终回答**。turn `95ffa3c8`（21:47）只产出 19 个 completion token 的「田野点定下来了，我这就把信息写入项目状态，并实测两段关键数据：」，无任何 `tool_calls`。`web_host/agent/loop.py` 第 160–166 行把非空正文判为 `has_final_answer=True` → 跳过收敛块；`_looks_truncated` 命中「：」→ 以 `tools=None` 续写 → 模型无工具可调、返回空 → 悬空句原样落库。

同会话内该模式出现 3 次：msg7 结尾「先问最实际的：」（用户被迫回「？中断了。」）、msg11 对比表在「¥65/晚 ×」处硬截断（`×` 不在 `_INCOMPLETE_TAIL_CHARS`）、msg19。此外 `ToolCardProps` 不含 `error`，搜索失败卡只显示「本次查询无可用供给 · 未取得任何报价」，用户看不到可操作的失败原因。

## 2. 目标与非目标

**目标**

- 餐饮/市内交通估算由「单一混合日标准」改为「聚落类型 × 就餐方式」的表驱动区间，区间与口径随预算条目一起返回并在前端展示。
- 任何生活费/餐饮/市内交通数字都必须来自工具调用或用户原话，不得由模型自由发挥。
- 搜索工具对过去日期在调用前即拒绝，并给出可操作的替代日期；错误码可被模型与前端区分。
- 回合不会以「前导语」结束：有动作意图但无工具调用的回复要重问一次，仍失败则诚实标注中断。
- 用户可在对话末尾对中断的回答点「重新生成」。

**非目标**

- 不做地区差异化 CPI 表（本轮只按聚落类型 × 就餐方式，表值为西部农村基线）。
- 不改 MCP 工具数量门槛（`tests/test_registry_surface.py` 断言 `>= 14`，新增能力不破坏）。
- 不做多币种、不做按月/按周的住宿长租定价。
- 不改 `web_host/agent/loop.py` 既有的「多轮工具评论在持久化时被最后一轮覆盖」行为（独立议题）。

## 3. 设计 A：生活成本估算模型

### 3.1 新模块 `kernel/aggregation/living_cost.py`

纯函数、无 I/O、无 LLM；表值以「元/人·天」为单位。

```python
SETTLEMENT_TYPES = ("village", "town", "county", "city")
BOARD_MODES = ("self_cook", "shared_meal", "mixed", "eat_out")
```

餐饮区间表 `FOOD_RANGE[settlement][board_mode] = (low, high)`：

| 聚落 | self_cook 自炊 | shared_meal 搭伙 | mixed 混合 | eat_out 外食 |
|---|---|---|---|---|
| village 村 | 12–20 | 15–25 | 18–30 | 25–45 |
| town 镇 | 15–25 | 18–28 | 22–38 | 30–50 |
| county 县城 | 18–30 | 22–35 | 28–45 | 35–60 |
| city 城市 | 25–40 | 28–45 | 35–60 | 50–90 |

市内交通区间表 `TRANSPORT_RANGE[settlement] = (low, high)`：村 0–5、镇 3–10、县城 5–15、城市 8–25。

`BASIS` 常量（写入返回值，供卡片与正文引用）：

> 云南农村居民人均食品烟酒 5,351 元/年（≈14.7 元/人·天，自炊口径）、人均消费支出 16,214 元/年（≈44.4 元/人·天，含居住）；村内自炊/搭伙按此上浮取值，城市外食含游客溢价。

`estimate_living_cost(settlement_type="village", board_mode="mixed", days, people=1, include_local_transport=True) -> Envelope`

返回 `Envelope(ok=True, data=...)`，`data` 形状：

```json
{
  "settlement_type": "village",
  "board_mode": "self_cook",
  "people": 1,
  "days": 60,
  "caliber": "村内自炊 · 1 人 × 60 天",
  "items": [
    {"name": "餐饮", "per_day_low": 12.0, "per_day_high": 20.0,
     "amount_low": 720.0, "amount_high": 1200.0, "unit": "元/人·天×人数×天数"},
    {"name": "市内交通", "per_day_low": 0.0, "per_day_high": 5.0,
     "amount_low": 0.0, "amount_high": 300.0, "unit": "元/人·天×人数×天数"}
  ],
  "total_low": 720.0,
  "total_high": 1500.0,
  "assumptions": ["自炊按在家做饭、不含酒水", "村内步行/电动车为主，进城班车单列在大交通"],
  "basis": "（BASIS 常量原文）",
  "source": "estimate",
  "coverage": "estimate"
}
```

校验（失败返回 `Envelope(ok=False, error=ToolError(code="invalid_arguments", ...))`，hint 列出合法枚举）：`settlement_type`/`board_mode` 必须在枚举内；`1 <= days <= 365`；`1 <= people <= 20`。

`CapabilityMeta` 描述需显式写明：这是区间估算，驻村场景用 `village` + `self_cook`/`shared_meal`；不得把城市游客标准套到村里。args 模型每个字段带 `Field(description=...)`（会进 schema，指导模型选值）。

注册：`kernel/aggregation/living_cost.py` 模块级 `register(...)`；在 `kernel/__init__.py` 的聚合导入列表加入 `living_cost`（否则裸导入路径注册表缺项）。

### 3.2 `compose_itinerary` 复用与契约变更

- 移除 `MEALS_PER_PERSON_DAY_DEFAULT = 150.0`（连同其模块文档中的描述），改为 `DEFAULT_SETTLEMENT = "village"`、`DEFAULT_BOARD_MODE = "mixed"`。
- `ComposeItineraryArgs` 新增 `settlement_type: str = "village"`、`board_mode: str = "mixed"`；`meals_local_per_person_day: float | None = None`（`None` = 用表；给了单值则以该值同时作 low/high，保留人工覆盖能力，但 schema 里不再出现 150 这个锚点）。
- 预算条目由一条「市内与餐饮」拆为两条：
  - `餐饮（估算）`：`amount=amount_low`、`amount_low`/`amount_high`、`unit="元/人·天×人数×天数"`、`quantity=days`、`people=count`、`basis=<口径 + BASIS>`、`source="estimate"`。
  - `市内交通（估算）`：同上，`include_local_transport=False` 时不生成。
- 未显式传 `settlement_type`/`board_mode` 时，`basis` 追加「默认口径（调用方未指定聚落类型/就餐方式）」。
- `total` 仍为保守值（估算取区间下沿）；`BudgetBreakdown` 新增 `total_high: float | None = None`，等于各条目 `amount_high ?? amount` 之和。

### 3.3 `kernel/models.py` 契约

```python
class BudgetItem(BaseModel):
    ...
    amount_low: float | None = None    # 估算区间下沿（amount 已等于下沿）
    amount_high: float | None = None   # 估算区间上沿；报价类条目为 None
    basis: str = ""                    # 估算口径与数据依据（报价类留空）

class BudgetBreakdown(BaseModel):
    ...
    total_high: float | None = None            # 含估算上沿的合计；报价类条目 low=high
    over_budget_possible: bool = False         # total_high > budget_total：区间跨预算线
```

向后兼容：新字段均有默认值，旧 payload 反序列化不受影响。

`over_budget` 语义保持不变（下沿已超预算 = 确定超支，且仍只在 `coverage == "complete"` 时有意义）；`over_budget_possible` 补上另一半事实：上沿超而`total`未超时，说明「区间跨预算线」，前端必须显示「按下限不超、按上限可能超」，**不得**表述为「预算内」。两者在 `coverage != "complete"` 时一律为 `False`。

### 3.4 提示词成本口径（单一来源）

新增 `kernel/guidance/costs.py`：

```python
COST_GUIDANCE = (
    "成本口径：任何餐饮、生活费、市内交通的金额估算，必须先调用 estimate_living_cost"
    "（或 compose_itinerary，其预算条目已带区间与口径）；报数字时必须同时给区间、"
    "口径（聚落类型/就餐方式/人数/天数）与假设。驻村场景默认按村内自炊或搭伙，"
    "不得套用城市差旅、游客或酒店标准；工具未返回的金额一律标「待核验」，"
    "不得给出单点精确值，也不得把用户随口说的数字改写成工具输出。"
)
```

由 `web_host/agent/context.py::_TOOL_GUIDANCE` 与 `core/orchestrator.py::_TOOL_GUIDANCE` 共同拼接（两处各加一行 `COST_GUIDANCE`），并在两处工具清单文案里补上 `estimate_living_cost`。

### 3.5 前端

- `web/src/components/cards/types.ts`：`ToolCardProps` 增加 `error?: { code?: string; message?: string; hint?: string } | null`。`Timeline.tsx` 第 99–103 行把 `call.error` 一并传入（`CollapsedToolCard` 已在传）。
- `BudgetCard`：`BudgetItemView` 增加 `amount_low`/`amount_high`/`basis`；金额列在 `amount_high != null` 时渲染 `¥720–1,200`，下方副行显示 `basis`（截断到一行，hover 看全文）；汇总条在 `total_high != null` 时补一行小字「含估算区间，上限约 ¥X」；`over_budget_possible && !over_budget` 时状态 chip 用「跨预算线」赭色（不显示「预算内」）。
- 新增 `LivingCostCard`（`estimate_living_cost` 的卡，约 80 行）：卡头「生活成本估算」+ 基准 chip；两行区间明细 + 合计区间；`assumptions` 与 `basis` 用小字列出。注册到 `web/src/components/cards/registry.tsx`。
- 搜索结果失败卡（`SearchResultCard`）：`error` 存在时，空态 `Notice` 优先显示 `error.hint`，其次 `error.message`，最后才是现有「本次查询无可用供给」。`hintKey` 逻辑保持不变。

## 4. 设计 B：回合健壮性

### 4.1 今日日期锚点

`kernel/guidance/phases.py` 新增：

```python
def time_anchor(today: date | None = None) -> str:
    """回合 system prompt 的时间锚点（每个回合现算，不缓存）。"""
```

文案：`今天是 2026-10-18（周日）。用户提到「暑假」「下个月」「明年」等相对时间时，先按今天换算成具体日期（YYYY-MM-DD）再调用任何搜索工具；实时搜索只接受今天及以后的日期，过去日期会被拒绝并返回 flyai_invalid_date。`

拼装顺序改为 `[time_anchor(), phase_prompt(phase), _TOOL_GUIDANCE, snapshot]`（`web_host/agent/context.py::build_system_prompt` 与 `core/orchestrator.py` 第 246 行同步）。

### 4.2 搜索原语前置日期校验

`kernel/sources/flyai.py` 新增：

```python
def _future_date_envelope(value: str, field: str, today: date) -> Envelope | None:
    """过去/格式非法日期 → flyai_invalid_date 包络；合法或空串 → None。"""
```

- 解析失败：`code="flyai_invalid_date"`，`message=f"{field}格式非法：{value}"`，`hint="日期格式应为 YYYY-MM-DD"`。
- 早于今天：`hint` 必须包含今天与建议日期（同月日 +1 年，若仍过去则再 +1 年），例如「实时搜索仅支持今天（2026-10-18）及以后的日期；若计划 7 月中旬出发，请改用 2027-07-15 重试」。
- 接入点：`search_flights`（`dep_date`/`back_date`）、`search_trains`（同）、`search_hotels`（`check_in`/`check_out`）。校验在拼 argv 之前，不发起子进程。
- `_error_envelope` 增补映射（纵深防御）：`"出发日期非法" in message` 或 `"invalid date" in low` → `flyai_invalid_date`，hint 与上同。
- 保留 `date.today()` 可注入（模块级 `_today()`），便于测试固定日期。

### 4.3 前导语识别与重问

`web_host/agent/loop.py`：

```python
_PREAMBLE_TAIL_CHARS = "：:，,、；;—…"
_PROMISE_MARKERS = ("我这就", "马上", "我先", "接下来", "下面", "让我", "这就去",
                    "先来", "我来", "实测", "查一下", "查一查")

def _looks_like_preamble(text: str) -> bool:
    """有动作意图但没有落地的短回复：结尾未完标点，或短句含承诺性动作词。"""
```

判定：`text.strip()` 非空，且（以 `_PREAMBLE_TAIL_CHARS` 结尾 → 是）或（长度 ≤ 120 且不以 `。！？` 结尾 且 含 `_PROMISE_MARKERS` → 是）。

**与既有续写机制的分工（必须同步改）**：冒号不再进 `_INCOMPLETE_TAIL_CHARS`——「以冒号结尾」一律走前导语重问（带 tools），而 `_looks_truncated` 的续写（tools=None）只负责 `finish_reason == "length"` 与逗号/顿号/破折号/省略号/开引号这类句中截断。两套机制不再对同一字符双重触发，既有测试 `test_half_sentence_stop_triggers_continuation` 的预期随之改为「带 tools 重问一次」。

主循环改法（第 160–166 行）：无 `tool_calls` 时先判前导语。

- 是前导语且本回合尚未重问过：把 `{"role": "assistant", "content": text}` 追加进 `messages`，再追加 system 指令「你上一条只写了前导语，没有执行任何工具调用。请立刻调用所需工具，或直接给出完整回答；不要重复前导语。」，`preamble_retried = True`，`continue`（`iterations` 照常计入，受 `max_iters`/`max_tool_calls`/`max_wall_seconds` 约束）。
- 重问后仍只出前导语：不置 `has_final_answer`，跳出主循环，交给既有收敛块；收敛块仍拿不到正文时，落库文本追加诚实说明「（本轮在调用工具前中断，可点「重新生成」）」而不是原样悬空句。
- 非前导语：维持现行为。
- 前导语重试期间已流出的文本不得丢失：新增 `stream_prefix` 变量，重问前记下 `accumulated_reply`；此后每一轮只要终止帧带非空正文，都取 `accumulated_reply = stream_prefix + 正文`（重问可能先调工具再出正文，必须每轮都拼，不能只在第一次拼）。

`_INCOMPLETE_TAIL_CHARS` 补齐 `×÷≈+-=`（这些符号不会出现在句末）并移出 `：:`；`|` 不加（完整表格行也以 `|` 结尾）。

### 4.4 回合终态标记

`turn_done` 事件新增 `"incomplete": bool`：收敛块走到兜底文案、或最终文本被判为前导语时为 `true`。前端据此在末条助手消息下方显示提示。

### 4.5 重新生成

**仓库层**：`kernel/repository/protocol.py` 增加

```python
def pop_last_message(self, pid: str, role: str | None = None) -> dict | None:
    """删除最后一条（可选按 role 过滤）消息并返回它；无匹配返回 None。"""
```

- `json_repo`：读 `chat_history.jsonl` → 从尾部找首条匹配 → 重写该文件（临时文件 + `os.replace`）→ 同步裁剪 `state["dialogue_context"]` 窗口 → `_save`。
- `sqlite_repo`：按 `seq` 倒序取一条 `DELETE`，同步 `updated_at`。
- 两侧行为一致，均返回被删条目（含 `role`/`content`/`ts`/`metadata`）。

**接口层**：`POST /projects/{project_id}/turns/{turn_id}/retry` → `201 {"turn_id", "status"}`。

- 归属校验同既有路由（`checked_owned_project`、`checked_turn_id`）。
- 回合必须存在且处于终态，否则 `409`。
- `tm.is_writing(pid)` 为真 → `409`（与创建回合同语义）。
- 取全量史最后两条：必须为 `[user, assistant]`；否则 `409`，detail `{"code": "retry_not_applicable", "message": "仅能重跑最后一条已中断的回答"}`。
- `repo.pop_last_message(pid, "assistant")`、`repo.pop_last_message(pid, "user")`，用弹出的 user 文本走既有 `_execute_turn`（`run_turn` 会重新 append 该 user 消息，历史无重复）。
- 客户端 key 解析复用 `_resolve_clients`。

**前端**：

- `rest.retryTurn(projectId, turnId)`。
- `useTurn` 增加 `retry(turnId)`：先 `conversation.dropLastAssistant()`（新增 store 动作，弹出末条 assistant），再 `beginTurn()` 并订阅新回合 SSE；`busy`/`conflict` 语义与 `send` 一致。
- `Timeline` 末条 assistant 消息下方渲染「重新生成」按钮（`turnRunning` 时禁用）；`incomplete` 为真时按钮旁显示赭色小字「本轮生成中断」。

## 5. 兼容性与迁移

- `BudgetItem`/`BudgetBreakdown` 新字段全部可选，旧项目状态、旧事件流、旧前端 payload 均可反序列化。
- `compose_itinerary` 的 `meals_local_per_person_day` 由 `float=150` 改为 `float|None=None`：显式传值的调用方行为不变；未传者改走表值（这是本次要达到的效果）。
- `estimate_living_cost` 是新增能力：`build_tool_schemas()` 数量 14 → 15，`tests/test_registry_surface.py` 的 `>= 14` 断言不破；`core/orchestrator.py::_CAPABILITY_DISPATCH` 必须同步登记（`test_capability_dispatch_covers_all_registered_tools` 是钉子）。
- 前端 `ToolCardProps` 新增可选 `error`，未传时行为不变。
- 生产部署为 tar 包覆盖（`/opt/fieldwork-agent`，无 git），本次需重新打包 `web/dist` 与 Python 包并重启 `fieldwork-web.service`。

## 6. 测试计划（先红后绿）

**Python**

- 新增 `tests/test_living_cost.py`：枚举校验、`village/self_cook` 60 天 = 720–1200、`include_local_transport=False`、`people>1` 线性、`days` 越界、schema 注册（`estimate_living_cost` 在 `build_tool_schemas()` 且无 `client` 注入参数）。
- `tests/test_itinerary.py`：预算条目由 1 条变 2 条；`amount == amount_low`；`total_high > total`；显式 `meals_local_per_person_day=30` 时餐饮 low=high=30×人数×天数；`settlement_type="city", board_mode="eat_out"` 时金额显著高于 village；`basis` 含默认口径提示。
- `tests/test_sources_flyai.py`：过去日期不启动子进程（mock `_run_command` 断言 0 次调用）、返回 `flyai_invalid_date` 且 hint 含今天与建议日期；未来日期正常透传；「出发日期非法」经 `_error_envelope` 映射为 `flyai_invalid_date`。
- `tests/test_web_agent_loop.py`：
  - 前导语（「…并实测两段关键数据：」+ 无 tool_calls）→ 触发一次带 tools 的重问，第二次返回正文 → 落库文本含正文且 `incomplete=false`；
  - 前导语重问后仍为空 → 落库文本追加中断说明、`turn_done.incomplete=true`；
  - 正常短回答（以「。」结尾）不触发重问；
  - `stream_prefix` 拼接口径：重问前后文本均不丢。
- `tests/test_repository_*.py`（json 与 sqlite 各一组）：`pop_last_message` 无过滤/按 role/无匹配、jsonl 与 state 窗口同步、sqlite `updated_at` 推进。
- 新增 Web API 测试：`retry` 正常路径（历史末两条为 [user, assistant] → 201）、非末条 409、忙时 409、越权 404。

**前端（vitest）**

- `toolCards.spec.tsx`：BudgetCard 渲染 `¥720–1,200` 与 basis；SearchResultCard 在 `error.hint` 存在时优先显示它。
- 新增 `livingCostCard.spec.tsx`：区间、合计、assumptions/basis 渲染；空 data 落 NoData。
- `useTurn.spec.tsx`：`retry` 会调用 `dropLastAssistant` 并订阅新回合；`incomplete` 时 Timeline 显示「重新生成」与中断提示。
- `rest.spec.ts`：`retryTurn` 的方法与路径。

**回归**：`pytest`、`ruff check .`、`npx tsc --noEmit`、`npm test`。

## 7. 风险与边界

- 表值是静态基线，不是实时行情：卡片与正文必须带 `basis`，不得表述为「当地实际价格」。用户若拿到实地价格，应用户要求可覆盖（`meals_local_per_person_day`）。
- 前导语判定是启发式：宁可多问一次（多一次 LLM 调用），也不要把悬空句当答案。重问只做一次，避免与预算上限打架。
- 「重新生成」会删除末条 assistant 与对应 user 消息后重跑：必须校验末两条形状，避免用户已发新消息时误删。
- 今日日期取自服务器时区（`date.today()`）；生产为单一区域部署，暂不做用户时区换算。
