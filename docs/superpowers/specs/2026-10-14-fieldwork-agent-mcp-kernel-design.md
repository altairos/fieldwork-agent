# 田野罗盘 Agent — MCP 内核化与双宿主架构设计

- 日期：2026-10-14
- 状态：已与用户逐节确认
- 范围：内核重构 + MCP Server + FastAPI Web 宿主 + React 前端 + SQLite 演进 + 测试策略

## 0. 目标与定位

将现有 CLI 单体的 Fieldwork Agent 演进为：

1. **无状态能力内核**（Python 包），能力可独立测试、独立演化；
2. **MCP Server 薄壳**：细粒度 tools/resources/prompts，供 WorkBuddy 等外部宿主 Agent 接入；
3. **FastAPI Web 宿主 + React 完整产品化前端**：自建宿主驱动四阶段访谈，用于展示与真实使用。

已确认的方向性决策（不在本文再论证）：

- 目标：长期产品/工具演进（非短期比赛冲刺）；
- MCP 粒度：**方案 1 细粒度无状态工具**，访谈编排由宿主负责；
- Web 宿主与 MCP Server 关系：**做法 A 共享内核、双宿主薄壳**（Web 进程内直调 kernel，不走 MCP 协议；`demo_mcp_client.py` 单独证明协议路径可用）。

## 1. 总体架构与内核模块边界

### 1.1 分层

```
┌─ 宿主层（薄壳，互不依赖）───────────────────────────┐
│  mcp_server/   web_host/(FastAPI)   cli/(现有,过渡期保留) │
├─ 能力内核 fieldwork_agent/kernel/ ──────────────────┤
│  sources/  aggregation/  documents/  guidance/  repository/ │
│  models.py  llm.py                                      │
├─ 外部依赖 ─────────────────────────────────────────┤
│  flyai CLI(subprocess)   DashScope SDK   JSON文件→SQLite │
└────────────────────────────────────────────────────┘
```

依赖方向单向：宿主 → kernel → 外部；kernel 不认识任何宿主（延续 DEVELOPMENT.md 约定）。

### 1.2 kernel 五模块及出身

| 模块 | 职责 | 与现有代码关系 |
|------|------|----------------|
| `kernel/sources/flyai.py` | 8 个原子搜索原语，返回**归一化结果**；覆盖范围＝国内 + 港澳台 + 海外主流目的地（2026-10-14 实测：曼谷航班/清迈民宿/加德满都文化POI/深港火车通票均可用）；航班/火车为 `journeys[].segments[]`+`ticketPrice` 形态、酒店/POI 为平铺形态，normalize 分形态处理；体验模式 `systemMessage` 透传为 `degraded` | 迁移自 `tools/flyai_tools.py` 直接映射部分；4 个组合策略方法剥离至 aggregation |
| `kernel/aggregation/site_profile.py` | `profile_field_site()`：并行调原语 → `FieldSiteProfile`（交通/住宿/文化/配套四段 + summary_hints）；海外偏远田野点（平台无村级供给）→ 住宿段置空并在 hints 给出"枢纽城市住宿 + 当地学术接待方安排进村"二段式建议 | 吸收 `plan_field_site_access`、`find_accommodation_for_field_work`、`analyze_field_site_culture_and_attractions` |
| `kernel/aggregation/itinerary.py` | `compose_itinerary()`：路线对比、分段住宿（房间数/入住假设显式）、调研节奏（适应/深入/补充期）、缓冲日（计入日历分配，与调研日不重复占用）、备选路线 → `ItineraryPlan`；**预算完整性契约（R2）**：`BudgetItem` 带币种/计价单位/数量/人数或房间数/报价时间/来源引用；`BudgetBreakdown.coverage ∈ {complete, partial, unknown}`，仅 complete 才允许得出"预算内/超支"结论；餐饮市内交通用"人均/天×天数"可调节数量单价模型（非总预算百分比）；长住日价未确认可订时标 `estimate/待核验`；交通选择同时展示价格+时长+换乘，最低价仅作排序 | 吸收 `comprehensive_travel_plan`；修复硬编码日期 bug；预算覆盖度为审阅意见 R2 新增 |
| `kernel/documents/proposal.py` | `compose_proposal(mode)`：结构化输入 → 研究方案 Markdown；`mode="global"`（默认，八章英式结构，适用海外/港澳申请与课程场景）已完整实现；`mode="kaiti"/"grant"`（开题版/国社科活页版，体例依据见 DEEP_RESEARCH 报告）为接口占位，参赛阶段后 flesh out；**documents/ 为 kernel 内唯一 LLM 消费模块**（proposal/guide/informants 生成共享此定位）；可选 `section` 参数单章重写；`compose_interview_guide()` 基于已确认对象档案（suggest 产物或用户提供，type 为自由文本）生成半结构式访谈提纲 | 迁移自 orchestrator `generate_full_proposal`/`_build_proposal_context`，上下文从拼字符串改为传结构化对象 |
| `kernel/guidance/phases.py` | 五阶段定义、system prompts、引导问题模板、推进判定纯函数 `check_advance(phase, research_context, field_sites, informants, has_itinerary, has_proposal, proposal_confirmed)`（三态区分见公理 4，非访谈型可 `interviews_not_applicable` 跳过访谈硬条件，D→finished 需 `proposal_confirmed`）；阶段 A 首要采集**输出用途/用户身份**（海外港澳申请/高校开题/基金申报/课程作业 → `ctx.purpose`，映射 compose_proposal.mode） | 拆自 `DialogueEngine`；`decide_tool_calls` 的关键词路由**整体废弃**（职责由 MCP 工具 schema + 宿主 LLM 接管） |
| `kernel/guidance/extract.py` | 地点/研究者信息提取纯函数 | 迁移自 `_extract_locations_from_message`、`extract_researcher_info_from_message` |
| `kernel/guidance/informants.py` | 示范档案库 `INFORMANT_EXAMPLES`（人工精选 5-6 类高质量词条：村落长老/妇女群体/宗教人士/基层干部/返乡务工者等）：**不是枚举白名单**，而是 `InformantCandidate` 六字段（approach/cautions/question_domains/ethics/access_channel/preparation）的写法示范（few-shot 质量锚）与无 key 降级兜底；附纯函数 `match_examples(topic)`（兜底粗匹配） | 新增（学术纵深） |
| `kernel/documents/informants.py` | `suggest_informants(research_topic, discipline, site_profile?, llm)`：LLM 按研究主题**动态生成 4-6 类候选访谈对象**（含敏感群体等主题专属角色），pydantic schema 校验结构完整性，宿主呈现、用户最终决定后 `save_informant`；无 llm 时降级为 `match_examples` 兜底并标注"参考起点，非完整覆盖"；用 proposal 档模型（低频、学术建议质量敏感） | 新增（用户决策 2026-10-14：固定库改为混合式生成） |
| `kernel/repository/` | `protocol.py`（`ProjectRepository` Protocol）、`json_repo.py`（承接 state.py）、`sqlite_repo.py`（里程碑2）。**kernel 能力函数不碰 repository**，落盘由宿主显式执行 | 拆自 `core/state.py`：状态读写归 repository，PHASES_ORDER/推进语义归 guidance |
| `kernel/models.py` | pydantic 数据契约：`FieldSiteProfile`、`ItineraryPlan`、`BudgetBreakdown`、`ResearchContext`、`ProposalDoc`、`InformantCandidate`（六字段候选档案）、统一返回包络 | 新增 |
| `kernel/llm.py` | DashScopeChat 迁入 + function-calling 支持（OpenAI 兼容端点）+ 模型分档配置 | 迁移自 `tools/dashscope_tools.py` |

### 1.3 核心公理

1. **能力函数不隐式读写项目状态**（R5 措辞修正）：入参→出参，副作用由宿主经 repository 显式执行；但能力依赖外部服务（搜索/LLM），**不是纯函数**——无 key/体验模式/源失败都可能发生，宿主必须按包络处理降级。
2. **LLM 只出现在两处**：`documents/` 能力实现（内核内，经注入客户端）与宿主 Agent 循环（内核外；共享循环位于 `runtime/` 模块而非 kernel，R5 修正）。搜索/聚合返回轻量 `summary_hints`（纯统计），不做解读文案。
3. **生长式迁移**：`kernel/` 与现有 `core/`、`tools/` 并存，逐能力迁移+测试转绿；`orchestrator` 改为 kernel 之上第一个宿主（CLI 变薄壳），最后删除旧决策逻辑。每步 CI 可过。
4. **三态区分（R3 新增）**："信息齐备"（`check_advance` 可判）、"草稿已生成"（产物存在）、"用户已确认"（`ConfirmationRecord`：确认时间+关键输入哈希）是三个独立状态；阶段推进可基于前两态（允许先探索），但项目完成（D→finished）必须要求 `proposal_confirmed`；关键输入（日期/地点/人数/预算）变化后，依赖产物按输入哈希不匹配自动标记 `needs_revalidation`，旧确认不得沿用于新方案；非访谈型研究可记录 `interviews_not_applicable + 理由` 跳过访谈对象硬条件。

### 1.4 模型分档（用户指定）

```
FA_INTERVIEW_MODEL = qwen3.8-flash   # Web agent loop：访谈主持+function calling（高频、便宜）
FA_PROPOSAL_MODEL  = qwen-max        # compose_proposal / compose_interview_guide（低频、质量敏感）
FA_EXTRACT_MODEL   = qwen3.8-flash   # 结构化信息提炼小任务
```

现有 `Config.chat_model`/`system_model` 迁移时改名对齐三槽，env 可覆盖。

## 2. MCP Server 清单

官方 `mcp` Python SDK，stdio 传输；Streamable HTTP 留作后续可选。Server 是 kernel 薄壳：schema 翻译 + 参数校验 + repository 接线，零业务逻辑。repository 暴露为工具（MCP Server 作为宿主有权组合 kernel+repo；约束只针对 kernel 内部）。

### 2.1 Tools（22 个，四类）

**① 原子搜索（8 个，全部独立暴露）**：`search_flights`、`search_trains`、`search_hotels`、`search_pois`、`search_keyword`、`search_ai`、`search_marriott_hotels`、`search_marriott_packages`——与 sources 一一映射，只读；返回归一化 `items[]`。description 写明田野场景语义（宿主 LLM 靠 schema+description 选型，替代关键词路由）。

**② 聚合能力（2 个，只读不落盘）**：
- `profile_field_site(location, budget_per_night?, interests?, departure_city?, travel_date?, max_items_per_section?)` → `FieldSiteProfile`（默认截断防宿主上下文爆炸）；
- `compose_itinerary(departure, destinations[], start_date, duration_days, budget_total, researcher_count)` → `ItineraryPlan`。

**③ 文档能力（3 个，内部用 LLM）**：
- `suggest_informants(research_topic, discipline, site_profile?)` → `list[InformantCandidate]`：按研究主题动态生成候选访谈对象（六字段 schema 校验），宿主呈现、用户确认后 save_informant 落盘；无 key 时示范库粗匹配降级；
- `compose_proposal(结构化 context 或 project_id, section?, mode?="global")` → Markdown；无 key 时模板拼接降级；`mode` 枚举 `global/kaiti/grant`，非 global 当前回退 global 提纲；**R1 契约**：`ProposalDoc.requested_mode`（用户所选）与 `mode`（实际生成）分开记录，回退时在文档**头部前置声明**能力限制（不仅尾注）；**文献红线**：参考文献仅用用户提供的知识库条目，缺材料时输出"待补检索清单"，禁止模型编造引用；
- `compose_interview_guide(research_topic, informant_types[], project_id?)` → 每类对象的半结构式提纲（开场/主问/追问/敏感问题处理/知情同意话术），供提案第三章引用；informant_types 为自由文本（suggest 产物或用户自定，不限于示范库）。

**④ 状态与引导（9 个，Server 接线 repository/guidance）**：
`create_project`、`list_projects`、`get_project`、`update_research_context`、`save_field_site`、`save_informant`、`add_knowledge_entry`、`search_knowledge`（FTS5 版）、`check_phase_advance(project_id)` → `{can_advance, to_phase, missing_fields}`。

**阶段推进条件扩展**：B→C 需"有 confirmed 田野点 且 已登记 ≥1 个访谈对象"。

### 2.2 Resources

```
fieldwork://guidance/phases                  四阶段流程总览+推进条件
fieldwork://guidance/questions/{phase}       引导问题模板
fieldwork://guidance/checklists/{phase}      需采集字段清单
fieldwork://guidance/informants/library      示范档案库（写法示范+降级兜底，非枚举白名单）
fieldwork://guidance/informants/{type}       单一示范词条完整档案
fieldwork://guidance/interview-guide/methodology  半结构式访谈方法论
fieldwork://templates/proposal-outline       八章 Proposal 模板
fieldwork://project/{id}/summary             项目快照
fieldwork://project/{id}/sites               已存田野点及画像
fieldwork://project/{id}/informants          已登记访谈对象清单
```

### 2.3 Prompts（MCP 第三原语）

`fieldwork_interview`（主持四阶段访谈，附资源引用）、`refine_proposal_section`。

### 2.4 统一约定

- 返回包络：`{ok, data?, error?: {code, message, hint}, degraded?}`；flyai 缺失返回 `ok:false, code:"flyai_unavailable"`，不抛异常；体验模式（无正式 Key，价格脱敏/结果受限）时 `systemMessage` 透传进 `degraded`。
- annotations：①②组 `readOnlyHint + openWorldHint`；④组 `destructiveHint:false`。
- `project_id` 只出现在状态类与 `compose_proposal(from_repo)`；搜索/聚合类不带。

## 3. FastAPI Web 宿主（Agent Loop）

### 3.1 Schema 单一来源

kernel 能力函数的 pydantic 入参模型 → JSON Schema，`build_tool_schemas()` 同时供 MCP Server 注册与 Web 宿主 LLM tools 参数。结构性保证"Web 能做的，WorkBuddy 也能做"；契约测试锚点（见 6.L3）。

### 3.2 目录

```
web_host/
├── app.py            FastAPI 入口，CORS(localhost)
├── api/sessions.py   POST /projects · GET /projects/{id} · GET /projects
├── api/chat.py       POST /projects/{id}/messages → SSE
├── api/exports.py    GET /projects/{id}/proposal · 下载 md
├── agent/loop.py     InterviewAgent 核心循环
├── agent/registry.py build_tool_schemas() + dispatch 表
├── agent/events.py   SSE 事件模型（pydantic）
└── sse.py            SSE 编码、心跳、last-event-id 重连
```

### 3.3 Agent Loop（每轮用户消息）

```
1. 组装上下文：system = guidance.phase_prompt(phase) + 工具使用指引 + 项目快照摘要；
   history = repo.get_recent_messages(30) + 新消息
2. LLM：qwen3.8-flash，OpenAI 兼容端点 /compatible-mode/v1/chat/completions，tools 参数，stream=True
3. 有 tool_calls：
   a. 每个 call 发 tool_started
   b. 无依赖 call 并行（asyncio.gather；同步 flyai subprocess 走 asyncio.to_thread）
   c. 发 tool_completed（完整结构化 payload；单条 ≤2000 字符进 LLM 消息）
   d. role=tool 追加 → 回步骤 2（最多 8 迭代）
4. 无 tool_calls → 最终文本（message_delta token 级流式）
5. 轮末：repo.append_chat；本轮搜索/画像结果 repo.cache_search；
   check_phase_advance → 可推进则更新 phase 并发 phase_transition
```

取舍：LLM 选型完全交给 function calling（零关键词路由）；阶段推进判定确定性（`check_advance`），不由 LLM 宣布，防幻觉推进；**回合内预算除迭代数外，还限总时长/工具调用数/费用上限（R5）**；工具结果进 LLM 消息用结构化摘要（条数/极值/coverage/完整结果引用）而非直接截 JSON（R5）。

**回合生命周期协议（R4，Web 宿主与 MCP 并存时的硬契约，写 Plan 3 时逐条落实）**：创建回合与订阅事件分离（POST 返回 `turn_id`，事件流按 `turn_id` 订阅）；请求带幂等键，重复提交/重连返回原回合不重执行工具；每事件带单调序号，前端对全部事件去重（非仅工具结果）；回合状态机 `running/completed/failed/cancelled/interrupted`，唯一终止事件；事件保留期限与过期后回放降级为快照恢复；用户消息/工具结果/状态变化即时持久化（不等轮末）；取消=停止后续调度，迟到结果落盘但不再改变回合状态；同一项目同时只允许一个写回合，MCP 与 Web 并存时写入经 repository 单写者协调；进程重启后遗留 running 标记 `interrupted` 并提供显式恢复入口。若协议所需的事件持久化 JSON 层扛不住，**触发条件提前落地最小 SQLite**（原 §5 里程碑 2 的开关条件相应前移）。

### 3.4 SSE 事件协议

| event | payload | 用途 |
|-------|---------|------|
| `message_delta` | `{text}` | 打字机正文 |
| `tool_started` | `{call_id, tool, args, purpose}` | 活动指示条 |
| `tool_completed` | `{call_id, ok, data, degraded?}` | 结果卡片唯一数据源（不经 LLM 转述，数字不失真） |
| `phase_transition` | `{from, to}` | 阶段进度条动画 |
| `turn_done` | `{reply_id, usage}` | 收尾、token 统计 |
| `error` | `{code, message, hint}` | 降级横幅 |

工具失败不中断回合（LLM 拿结构化失败自行解释/换策略）。

### 3.5 会话与部署

project 即会话；内存 loop 注册表支持取消（`POST /messages/{id}/cancel`）。`uvicorn web_host.app:app --port 8300` + 静态 React build；开发期 Vite proxy。本地单人，无鉴权。Web 宿主进程内直调 kernel；`demo_mcp_client.py` 单独验证 MCP 协议路径。

## 4. React 前端

### 4.1 选型

Vite + React 18 + TypeScript + Tailwind CSS + zustand + react-markdown/remark-gfm。不引重型组件库；视觉规格实现阶段配合 frontend-design 技能。

### 4.2 路由（3 页）

```
/                ProjectHub      项目列表+新建（学科倾向/快速模板）
/p/:id           Workspace       访谈工作台
/p/:id/proposal  ProposalView    提案全屏阅读/导出
```

### 4.3 Workspace 三栏

- 左栏：阶段导航（A 研究定位/B 田野点/C 行程/D 方案 + 状态徽标）、项目列表入口；
- 中栏：对话流（用户气泡、助手 markdown 流式、工具活动条、折叠工具结果卡、阶段推进横幅）、输入框；
- 右栏：上下文面板（采集进度清单/田野点卡片+画像面板/访谈对象表/预算累计条/知识库摘要）。

原则：中栏=时间线，右栏=状态投影，同源 `tool_completed` payload。**右栏不自动强切 Tab**：阶段推进时发切换建议 toast，用户确认才切（已确认）。

### 4.4 组件（tool→卡片 渲染器注册表，新增工具只加组件）

`TransportCard`、`HotelCard`、`POICard`、`SiteProfilePanel`、`ItineraryTimeline`（竖向时间线：适应/深入/补充期+缓冲日标色、备选折叠）、`BudgetBar`（累计 vs 预算，超支红警）、`InformantTable`、`ToolActivityStrip`（失败灰显+降级提示）、`PhaseBanner`、`ProposalViewer`（八章导航+正文+版本切换+下载 .md）。

### 4.5 前端 stores

`project.ts`（REST 快照+phase 事件更新）、`conversation.ts`、`tools.ts`（call_id→payload 索引）、`ui.ts`。SSE 断线按 last-event-id 补发；`error` → 顶部降级横幅。

## 5. 持久化演进（JSON → SQLite）

### 5.1 原则与里程碑

Repository Protocol 是稳定边界，宿主只见 Protocol。三步走：

```
里程碑1: JsonRepository 实现 Protocol（承接 state.py 逻辑），默认启用
里程碑2: SqliteRepository 实现同一 Protocol，FA_STORAGE=sqlite 可切换，双实现并行
里程碑3: fieldwork migrate --to-sqlite 搬迁存量，验证后 JSON 降级为只读兼容层
```

### 5.2 Schema（stdlib sqlite3，无 ORM，`PRAGMA user_version` 管版本）

```sql
projects        (project_id PK, created_at, updated_at, phase, phase_history)
research_context(project_id PK, data)
messages        (id PK, project_id, role, content, meta, ts)     -- 全量，废除50条滚动+jsonl溢出双轨
field_sites     (id PK, project_id, name, confirmed, profile, created_at)
informants      (id PK, project_id, code, type, relation, contact, notes)
search_cache    (id PK, project_id, query, tool, result, cached_at)  -- result ≤64KB
knowledge       (id PK, project_id, title, content, source, category, tags, created_at)
proposals       (id PK, project_id, kind, version, content, created_at)  -- kind: proposal/interview_guide
schema_meta     (key, value)
```

`messages`、`knowledge` 挂 FTS5 虚表（`search_knowledge` 全文检索）。收益：消息全量单轨、单文件库备份、WAL+`busy_timeout=5000` 支持 MCP/Web 双进程并发读（本地单人写冲突罕见；迁移期指定单一写者并写入 README）。

### 5.3 提案文档

`content` 存 DB 为事实源，按需导出 `.md` 文件（已确认）。`proposals.version+kind` 替代 `proposal_draft/final` 两个单值字段，支持多稿迭代。

### 5.4 迁移命令

`fieldwork migrate --to-sqlite [--project-id X]`：扫描存量 `state.json` → 逐项目导入七类数据 → 行数+抽样字段哈希校验 → `schema_meta` 写 `migrated_from` 标记 → 原 JSON 目录原地保留（天然回滚）。幂等。

## 6. 测试与契约策略（四层 + 评估集）

延续现有 harness 资产：全 mock、不联网、CI 直接跑。

- **L1 Kernel 单测**：flyai argv 拼装（迁移现有）+ 新归一化测试（价格解析/字段映射/缺字段容错）；aggregation 用 fixture itemList 断言预算筛选边界（上浮50%）、原语失败隔离、summary_hints 统计；guidance `check_advance` 全覆盖（含访谈对象新条件）+ 模板无占位符快照；proposal 上下文组装完整性（缺渠道即断言失败）+ 无 key 降级路径。
- **L2 Repository 契约**：`tests/repository_contract.py` 约 30 个抽象用例参数化跑 Json/Sqlite 双实现；含迁移 fixture（旧散 JSON 目录 → 导入后规范化 state 全等）。
- **L3 宿主同构契约**：① Schema 同构测试——`build_tool_schemas()` 与 MCP Server 注册清单逐一对齐（名称/参数 JSON Schema/description 哈希，SDK `types.Tool` 序列化对比，不起进程）；② Server 冒烟——in-memory client 跑 list_tools/list_resources/每 tool 一个代表调用，断言统一包络与 `flyai_unavailable` 降级；③ Web loop 测试——fake LLM 脚本化 tool_calls 驱动 loop.py，断言并行分组、8 迭代上限、role=tool 格式、轮末副作用、SSE 事件配对（覆盖 Roadmap 的 orchestrator 流式测试欠账）。
- **L4 端到端冒烟（CI）**：Playwright 一条剧本（新建→阶段A→右栏清单打勾→阶段B卡片价格与 mock 一致→generate→八章可见），后端 fake flyai stub + 录制回放 fake LLM，无 key 可跑。真实链路发布前手动 `make demo-check`，不进 CI。
- **行为评估集**：`evals/scenarios/` 5-8 个 golden 对话脚本（含"云南白族村落宗族研究/2万/三个月"），检查点=调用工具集合/采集字段完整度/禁止行为（未确认田野点不得进阶段 C）。真模型手动跑，不进 CI 门禁；prompt/模型档位变更后的回归安全网。
- **CI**：扩展现有双平台矩阵：`ruff → L1+L2 → L3`（每次 push），`L4`（每次 push，装 Node）；L4 不允许静默 skip。
- **业务边界场景（R6，纳入 L1-L4 与评估集，防"流程跑通但方案不可用"）**：三个月驻村仅有短住报价→长住标估算/待核验；多点部分费用未知→coverage≠complete、预算不完整可见；修改已确认日期→旧行程/预算/提案失效可见；SSE 断线重连/重复事件→工具只执行一次、正文卡片不重复；取消时工具在跑→终止状态明确、迟到结果不翻转；无文献输入→不虚构引用；非访谈型研究→可跳过访谈对象完成流程；体验模式脱敏价→不当作零元；偏远点无供给→区分已搜到的枢纽方案与待联系的当地接待。

## 7. 边界与留作后续

- 开题版/基金申请版 proposal 模式的体例深化（完整改写提纲：创新之处/重点难点/框架大纲/研究基础/三科目预算/匿名规则；依据 `DEEP_RESEARCH_社科research-proposal体例核验.md`）——参赛阶段后启动；
- 当前产品重心：**行程安排 + 田野保障方案**（选点依据、进入田野策略/介绍信与知情同意流程、报道人接触与物资准备、食宿与通讯可达性）打磨到最优（用户决策 2026-10-14）；
- 向量化知识库检索（当前 FTS5 关键词/全文）；
- MCP Streamable HTTP 传输、多用户/鉴权、部署形态；
- DOCX/PDF/XLSX 导出与地图可视化（交付物专业化，作为后续独立设计）；
- 文献/已有研究检索接入学术纵深（方向 B 剩余部分，后续独立设计）；
- 钉钉同步保留现状，不迁移进 kernel 公共接口（宿主侧适配层，后续可择机移出 orchestrator）。
