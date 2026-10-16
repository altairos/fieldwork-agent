# 田野罗盘 — 调研时窗分析与预算证据链设计（飞猪 API 深度挖掘）

- 日期：2026-10-16
- 状态：已与用户逐节确认（方向裁决：比赛亮点优先 → 方案三"分阶段全上"）
- 范围：飞猪数据深层解析（M1）+ 新聚合能力 `analyze_fieldwork_window`（M2）+ 搜索快照证据链与 proposal/前端接线（M3）

## 0. 目标与定位

把飞猪数据从"报价来源"升级为**"研究设计决策依据"**。三个具体跃迁：

1. **时窗决策**：通用旅行产品回答"怎么去最便宜"，本能力回答"什么时候去才不错过核心观察场景"——多日期价格曲线 × 节庆覆盖 × 预算可行性的联合推理；
2. **证据链**：预算与行程数字可溯源到带时戳的搜索快照，"可溯源、可复核"从口号变功能；
3. **数据挖潜**：补齐 kernel spec §1.2 已承诺但迁移中未兑现的航班/火车深层结构解析与体验模式透传。

双重目标：比赛答辩亮点（数据驱动研究决策的完整故事）+ 真实产品能力（每个 milestone 独立可用）。

## 1. 现状核实与差距

| 项 | 现状 | 差距 |
|----|------|------|
| 8 原语接入 | `kernel/sources/flyai.py` 全部注册，schema 单一来源 | 无 |
| 航班/火车深层结构 | kernel spec §1.2 承诺 `journeys[].segments[]+ticketPrice` 分形态 normalize；当前 `_page_envelope` 只读 `data.itemList` 平铺 | **承诺未兑现**：起降时刻/时长/中转/席别若在嵌套结构中则整体丢失（spike 核实） |
| 体验模式透传 | spec 承诺 `systemMessage` → `degraded`；`Envelope.degraded` 字段存在，documents/itinerary 已用，**sources 层无处理** | **承诺未兑现**：体验模式脱敏状态在搜索层不可见 |
| 搜索缓存 | `ProjectRepository.cache_search`（64KB/truncated/`list_cached_searches`）已落地 | 缺"预算数字 ↔ 快照 id"引用关系；聚合能力内部 N 次调用宿主无法逐次落盘 |
| 时窗信息 | `ResearchContext.time_window` 为自由文本，由访谈采集 | 无数据支撑的时窗选择，预算与节庆脱节 |

**架构约束（不可违背）**：kernel 公理 1——能力函数不碰 repository，落盘一律宿主显式执行；kernel 无状态注入模式（`client`/`llm` keyword-only 注入）继续适用。

## 2. 总体架构与里程碑

```
M1 数据地基 ──→ M2 analyze_fieldwork_window ──→ M3 证据链闭环
spike 三验证      采样/评分/降级契约            RecordingClient + 宿主落盘
深层结构解析      LLM 节庆抽取（可降级）         proposal 引用 + 前端卡片
systemMessage 透传                              MCP/前端能力面 13→14
```

每个 milestone 结束时 CI 全绿（生长式迁移精神）；M1/M2 不依赖前端改动，M3 才动 web。

## 3. M1 — 数据地基

### 3.1 spike（先行，结论决定 M1 后半与 M2 参数）

脚本 `tools/spike_flyai_window.py`（**throwaway**，结论脱敏后进 spec 附录与 fixture，脚本本身不进 CI）：

1. **航班响应形态**：`search-flight --origin 昆明 --destination 大理 --dep-date <未来日>` 真实输出存档——核验是否存在 `data.journeys[].segments[]` 嵌套、`ticketPrice` 位置、起降时刻字段名；
2. **多日期采样耐受**：同航线连续 13 个日期点 + 13 次住宿查询，记录耗时/失败/限流信号；
3. **节庆关键词质量**：`keyword-search --query "大理 节庆 民俗 活动日历"` 与 `"火把节 2027 日期"` 两查，评估结果可抽取性。

**分支裁决**：若航班实际为平铺 `itemList`（无嵌套），3.2 缩减为 `extra` 字段定向挖潜（时刻/时长字段名对号入座），不阻塞 M2。

### 3.2 深层结构解析（`kernel/sources/flyai.py`）

- `_page_envelope` 分形态：存在 `data.journeys` 时展开 `segments[]`，每 journey 归一为一条 `SearchItem`：
  - `name` = `{航司}{航班号} {起飞时刻}-{到达时刻}`（火车同理：车次+席别）；
  - `price` 取 `ticketPrice`（经 `parse_price`）；
  - 新增结构化字段 `SearchItem.schedule: Schedule | None`（`dep_time/arr_time/duration_minutes/stops/cabin`，航班/火车共用，酒店/POI 为 None），未知字段仍进 `extra`——**不破坏现有消费者**（`extra` 语义不变，新字段可缺省）；
- `normalize_items` 不动（平铺路径行为不变）；分形态逻辑只在 `_page_envelope`，测试用真实形态 fixture 钉死。

### 3.3 体验模式透传

- `_page_envelope`/`_execute` 检测 payload 顶层或 `data` 内 `systemMessage` 非空 → `Envelope.degraded = str(systemMessage)`（成功包络照常带 data；上层 itinerary/site_profile/window 聚合时透传拼接，措辞对齐既有 degraded 风格）。

## 4. M2 — `analyze_fieldwork_window` 聚合能力

位置：`kernel/aggregation/window.py`；注册进 registry（`read_only=True, open_world=True`，与 8 原语同面）。

### 4.1 入参出参契约

```python
def analyze_fieldwork_window(
    departure: str, destination: str,
    range_start: str, range_end: str,        # YYYY-MM-DD，候选区间（建议 ≤ 120 天）
    duration_days: int, budget_total: float,
    sample_step: int = 7,                    # 采样步进（天）
    *, client: FlyAIClient | None = None, llm: Any = None,
) -> Envelope:  # data: WindowAnalysis
```

```python
class FestivalInfo(BaseModel):
    name: str                 # "彝族火把节"
    approx_dates: str         # "农历六月廿四，约7月下旬"（人类可读，LLM 原文）
    approx_start: str         # ISO 日期 "2027-07-26"——LLM 完成农历→公历换算
    approx_end: str           # ISO 日期 "2027-07-28"
    relevance: str            # 对研究主题的观察价值（LLM 抽取）

class PriceCurve(BaseModel):
    dates: list[str]                       # 采样点日期
    flight_min: list[float | None]         # 该日出发最低价（None=采样失败）
    hotel_min: list[float | None]          # 当晚入住最低价

class RecommendedWindow(BaseModel):
    start: str; end: str                   # YYYY-MM-DD（end = start + duration_days - 1）
    score: float                           # 0-100，确定性评分
    reasons: list[str]                     # 模板拼装的理由链（含统计数字）
    evidence: dict[str, Any]               # 七键评分明细（price/festival/budget/flight_min/hotel_min/estimate/festivals）（R9：原 list[str] 口径与 T6/T8 消费链矛盾，2026-10-16 裁决修正）

class WindowAnalysis(BaseModel):
    windows: list[RecommendedWindow]       # 按 score 降序，≤3 个
    price_curve: PriceCurve
    festivals: list[FestivalInfo]          # 可为空（抽取失败降级）
    budget_feasible: bool                  # 最优窗口预算是否可行
    degraded: str | None = None            # 体验模式/节庆降级/部分采样失败说明
```

### 4.2 采样与评分（确定性，LLM 不参与评分）

- **采样**：`[range_start, range_end]` 内每 `sample_step` 天一点，航班查 `dep_date=点`、住宿查 `check_in=点`；ThreadPoolExecutor 并行（复用 site_profile 先例）；采样点数上限 `MAX_SAMPLES=16`，超出自动放大步进并在 degraded 说明；
- **评分**（窗口在 `[range_start, range_end - duration_days]` 按 `sample_step` 滑动枚举；窗口内某日期价格取**最近采样点**的值——确定性、可测试，缺值点跳过）：
  - `price_score`（0-40）：窗口折扣率 `d = 1 - 窗口均价/全期均价`，得分 `min(40, 40×d/0.3)`（即优惠 30% 得满分）；均价 = 航班与住宿各自窗口均价的算术平均，某维度整类缺值时折叠为另一维度单独计；
  - `festival_score`（0-30）：窗口与 `approx_start~approx_end` 区间有交集的节庆每个 +15，封顶 30（无节庆数据时该项权重并入 price_score，映射改为 0-60，degraded 说明）；
  - `budget_score`（0-30）：窗口估算总预算（交通最低 × 往返 + 住宿谷值 × 夜数 + 餐费默认值，复用 itinerary 口径）`≤ budget_total` 得满份，超支比例 `r` 时得分 `max(0, 30×(1-r))`；
- **reasons 模板拼装**（可复现、可测试）：如 `"住宿均价较全期均值低 18%"`、`"窗口覆盖彝族火把节（核心观察场景）"`、`"估算总预算 ¥18,400 ≤ 预算上限 ¥20,000"`。

### 4.3 失败与降级语义（对齐 profile_field_site）

| 情形 | 行为 |
|------|------|
| 单采样点失败 | 该点价格记 None，曲线缺值，evidence 不引用 |
| 住宿/航班整类失败 | 对应曲线全 None，评分剔除该维度并 degraded 说明 |
| 节庆抽取失败 / `llm=None` | `festivals=[]`，权重并入价格维度，degraded 说明 |
| 体验模式（`Envelope.degraded` 透传） | 拼接进 `WindowAnalysis.degraded`，价格脱敏照常透传 |
| 全部采样失败 | `Envelope(ok=False, code="all_sources_failed")`，hint 对齐现有措辞 |
| 任何异常 | 不逃逸（`_safe_call` 模式） |

节庆抽取走 `llm.chat`（slot 复用 `interview` 档——低频、结构化抽取），prompt 要求返回 JSON 数组且**必须含公历 ISO 区间**（农历换算由 LLM 完成，内核只做确定性区间求交），解析失败即降级，不重试。

## 5. M3 — 证据链闭环与接线

### 5.1 RecordingClient（`kernel/sources/flyai.py` 新增，纯内存）

```python
class RecordingClient:
    """包装任意 client，逐次记录 (method, kwargs, envelope)——宿主据此落盘，不碰 repository。"""
```

聚合能力内部并行 N 次原子调用，宿主无法逐次拦截——**裁决**：宿主编排时注入 `RecordingClient(FlyAIClient(...))`，能力返回后宿主遍历 `records` 逐条 `cache_search`（query 序列化 method+kwargs，result 存归一化包络）。备选"宿主事后按 evidence 重查再落盘"被否：重复调用浪费且与能力执行时序不一致。kernel 仍无状态（RecordingClient 只收集不落盘，不违公理 1）。

### 5.2 宿主落盘与 snapshot_ids

- `web_host/agent` 编排：时窗/行程/画像能力的每次调用包一层 RecordingClient；落盘后将 cache id 列表附进 API 响应 DTO（新增 `snapshot_ids: list[str]`，SSE 事件透传）；64KB 截断（`truncated`）原样透传，前端展示"快照不完整需重查"提示；
- MCP Server：~~tools 返回的 content 附 `snapshot_ids` 字段~~——**descope（终审裁决 2026-10-16）**：快照经 web 宿主 REST 端点披露（前端 SnapshotDisclosure 消费），MCP 客户端拿到 ids 也无查询渠道（无消费方），复刻成本大于收益；候选回填见 backlog §2.8 F13；
- 二次生成 proposal 前宿主可先 `list_cached_searches` 命中复用（query 精确匹配），减少重复调用——**缓存命中不改变包络数据本身**。

### 5.3 proposal 接线

- 宿主把推荐时窗写入 `ResearchContext.time_window`（人类可读串：`"2027-07-20 ~ 2027-08-18（推荐时窗：覆盖火把节，住宿低于全期均值 18%）"`）；
- `BudgetItem.source` 格式扩展：`"flyai 快照 sc_xxx（2026-10-16 查询）"`——compose_itinerary 在宿主传入 snapshot 映射时拼入（映射经新增可选参数注入，默认 None 不影响现有调用）；
- proposal 第五章（行程安排）引用最优窗口摘要行——经 research_context 上下文渠道自然达成（时窗写入 `ResearchContext.time_window` 后，CLI `_snapshot` 与 web 宿主 system 组装均携带 research_context，proposal 生成时自然引用；终审裁决 2026-10-16，不引入硬编码 context_block，不改 documents 内部结构）。

### 5.4 前端（`web/src`）

- 时窗卡片：价格曲线迷你图（纯 SVG/CSS，不引图表库）+ 节庆标记 + 推荐窗口高亮，注册进 `toolCardRegistry`；
- 溯源展开：预算/行程条目点击展开快照原文（时戳 + 归一化结果，truncated 时提示）；
- `KERNEL_TOOLS` 对齐测试从 13 → 14（`web/tests/toolCards.spec.tsx`），`mcp_server/mapping.py` TOOL_GROUPS 同步加组。

## 6. 测试策略（四层金字塔对齐）

- **L1 单测**（`test_sources_flyai.py` 扩展）：分形态解析（spike 真实响应脱敏为 `tests/fixtures/flyai_responses/`）、`systemMessage` 透传、`Schedule` 归一化；
- **L2 聚合契约**（`test_window.py`，全 mock）：StubClient 模式（对齐 test_itinerary）——评分确定性（同输入同分数）、窗口枚举边界（区间过短/步进放大）、降级矩阵逐行、all_sources_failed；RecordingClient 记录完整性；
- **L3 宿主**：web_host dispatch 快照落盘 + `snapshot_ids` 透传、缓存命中复用；MCP mapping 对齐（14 能力）；
- **L4 前端**：toolCards 对齐、时窗卡片渲染、溯源展开交互；
- repository 契约：cache_search 用例已存在双跑覆盖；如新增方法（无计划）才动 `repository_contract.py`。

## 7. 风险与开放问题

| 风险 | 缓解 |
|------|------|
| 多日期采样触发限流 | spike 先验证；MAX_SAMPLES=16 硬顶；失败点跳过不重试 |
| 节庆数据质量不可控 | 抽取失败即降级（纯价格时窗仍成立）；relevance 仅作展示不进评分 |
| 航班无嵌套结构 | 3.2 分支裁决：缩为 extra 挖潜，不阻塞 M2 |
| SQLite worktree 合并时序 | 快照走 Protocol（JSON 先行）；合并后契约双跑自动覆盖，零改动 |
| approx_start/end 缺失或非法 ISO | 该节庆按"不覆盖"处理，宁缺勿错 |

开放问题（实现计划阶段裁决，不阻塞 spec）：采样步进是否按季节密度自适应；时窗卡片移动端布局。

## 8. 非目标

- 不做预订/下单（能力面严格只读）；
- 不做多目的地联合时窗优化（单目的地起步，`destinations` 复数扩展留待真实需求）；
- 不做 FTS5 检索（延续 Plan 5 裁决）；
- 不做采样失败自动重试风暴（跳过即止）；
- spike 脚本与真实响应原文不进版本库（脱敏 fixture 才进）。

## 附录 A — spike 结论（2026-10-16）

> 执行 `python tools/spike_flyai_window.py`（共两轮 + 诊断，累计约 61 次真实 API 调用；raw 存档于 untracked `.superpowers/sdd/2026-10-16-fieldwork-window-analysis/spike_raw/`；首轮因 helper 容错缺陷数据全弃后修复重跑，详见 task-1-report）。脚本保留在工作树、不进版本库。

### A1. 航班响应形态（验证 1）——journeys 形态无法证实，接口系统性业务失败

- 14 次调用中未触发 403 的 11 次（昆明→西昌 `--dep-date 2026-11-20` 单查 + 前 10 个采样日）全部返回该形态，后 3 个采样日为 403 风控：

  ```json
  {"data": null, "message": "搜索机票低价行业失败", "status": 1, "systemMessage": null}
  ```

- **无 `data.journeys`、无 `data.itemList`**；journey/segment 实际键名清单**不可得**，`ticketPrice` 位置不可考（非"平铺"，是 data=null）。
- **裁决落点（严于 §3.1"平铺"分支）**：Task 2 走**合成 fixture** 分支——3.2 的 journeys 分形态解析只能以合成数据钉测试；M2 时窗能力的 flight 维度在体验模式环境**不可用**，§4.3"整类失败→剔除维度并 degraded 说明"由边缘 case 升格为**必然而路径**。

### A2. 多日期采样耐受（验证 2）——hotel 可用但价格脱敏；风控为累计计数窗口

- 13 点（2026-11-20 ~ 2027-02-12，步进 7 天）× (flight + hotel)：
  - **flight 13/13 失败**：10 次"搜索机票低价行业失败"（status=1）+ 3 次 403 风控（第 21 次调用起）；
  - **hotel 后 4 点 403 失败（2027-01-22 ~ 02-12）；前 9 点推断成功**（脚本按 brief 原版仅记 error 键，未逐点存档；离线复测同参数 `2026-11-20` 确认通道正常：`status=0`、3 条真实酒店、中文参数无损）；
- **耗时**：命令平均 **1.6s/对**（不含对间 sleep 3s），无超时（timeout=60s 充裕）；
- **风控信号**：两轮独立运行（第二轮已含 3s 对间间隔）均在**累计第 ~21 次调用**触发 `MCP HTTP 403: "Abnormal access behavior detected by risk control"`——**计数窗口而非纯速率**，加间隔不防；403 后 `keyword-search` 仍可用（工具配额独立）；数分钟后窗口解除（复测成功）。M2 的并行采样必须配节奏/配额预算：**~20 次/窗口是硬约束**，MAX_SAMPLES=16 上限方向正确但需叠加工具级计数；
- **体验模式价格脱敏（关键）**：hotel `price` 为 `'¥5x'`/`'¥6x'` 形态（十位模糊化），实测 `parse_price('¥5x') → None`——**价格维度在体验模式下整体不可得**。M2 的 `PriceCurve`/`budget_score` 在此环境会全空，§4.3 降级矩阵需把"价格脱敏"列为一等公民降级路径（而非仅开发期妥协）。

### A3. 节庆关键词质量（验证 3）——可抽取性中等，标题有信号但无日期字段

- 两查均 `status=0`：`"西昌 节庆 民俗 活动日历"` **25 条**；`"彝族火把节 2027 日期"` **10 条**；
- **条目形态与 hotel 不同**：`itemList[]` 为 `{"info": {…}}` 单键嵌套，title 在 **`info.title`**（hotel 为平铺 `name`/`price`）；
- title 样本（query 1 高相关前 5）：《阿惹妞》彝文化实景剧演出门票（西昌）、西昌火把广场阿惹妞演出门票彝文化风情火秀+接驳车、大凉山秘境西昌螺髻山…6日游、西昌卫星发射中心基地参观、邛海泸山国家级风景名胜区…；另混有弱相关商品（洲际/锦江全国通兑酒店券等）；
- **可抽取性判断**：标题含"彝文化""火把""实景剧"等节庆民俗信号，LLM 从 titles 可抽取节庆名称与 relevance；但 `info` 内 `price`/`rate` 均 null、**结果不含任何日期字段**——公历区间只能靠 LLM 世界知识（火把节农历六月廿四≈7 月下旬）。§4.3"抽取失败即降级、宁缺勿错"的设计必要且大概率常态触发；
- 成功响应顶层带 `systemMessage`（体验模式提示原文：`*当前为体验模式，部分搜索结果可能受限…*`）——**§3.3 透传假设证实**（字段真实存在、非空、成功包络照常带 data）。

### A4. 附加环境发现（影响 Task 2-4 实现分支）

1. **Windows CLI 层（本机 spike 环境特有；Linux 部署不涉及；生产 `kernel/sources/flyai.py` 未动）**：flyai CLI（node）完成请求、完整 JSON 写入 stdout 后，**进程退出阶段** libuv 断言崩溃（rc=0xC0000409，stderr `src\win\async.c line 76`）——数据无损但 returncode 非零；spike helper 采用"stdout JSON 优先采信"（R3 原版 returncode 判定在 Windows 会全灭真实数据）。另：pwsh 管道 `[Console]::OutputEncoding`（cp936）仅在显示/中转层搅乱 utf-8；python→shim→node 的中文**参数**无损。
2. **生产代码缺口（建议 Task 2 纳入 3.2 范围）**：`normalize_items` 只读条目顶层键（`_NAME_KEYS=("name","title")`），keyword-search 的 `{info:{…}}` 嵌套条目归一化后 `name=""`、`price=None`——keyword-search 结果当前**实质无字段可读**。
3. **fixture**：因 A1 无 journeys 嵌套，本任务不创建 `tests/fixtures/flyai_responses/flight_journeys.json`，Task 2 用合成 fixture（brief Step 4 条件分支成立）。
