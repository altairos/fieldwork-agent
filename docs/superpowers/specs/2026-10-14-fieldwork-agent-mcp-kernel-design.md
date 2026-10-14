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
| `kernel/sources/flyai.py` | 8 个原子搜索原语，返回**归一化结果**（`¥1,234`→`float price`，统一 `name/price/jump_url/pic_url`）；subprocess argv 拼装保留 | 迁移自 `tools/flyai_tools.py` 直接映射部分；4 个组合策略方法剥离至 aggregation |
| `kernel/aggregation/site_profile.py` | `profile_field_site()`：并行调原语 → `FieldSiteProfile`（交通/住宿/文化/配套四段 + summary_hints） | 吸收 `plan_field_site_access`、`find_accommodation_for_field_work`、`analyze_field_site_culture_and_attractions` |
| `kernel/aggregation/itinerary.py` | `compose_itinerary()`：路线对比、分段住宿、调研节奏（适应/深入/补充期）、缓冲日、备选路线 → `ItineraryPlan` | 吸收 `comprehensive_travel_plan`；修复硬编码日期 bug（`check_out_dt = f"2026-12-{...}"`） |
| `kernel/documents/proposal.py` | `compose_proposal()`：结构化输入 → 八章 Research Proposal Markdown；**kernel 内唯一 LLM 消费者**；可选 `section` 参数单章重写；`compose_interview_guide()` 生成半结构式访谈提纲 | 迁移自 orchestrator `generate_full_proposal`/`_build_proposal_context`，上下文从拼字符串改为传结构化对象 |
| `kernel/guidance/phases.py` | 五阶段定义、system prompts、引导问题模板、推进判定纯函数 `check_advance(phase, research_context, field_sites, informants)` | 拆自 `DialogueEngine`；`decide_tool_calls` 的关键词路由**整体废弃**（职责由 MCP 工具 schema + 宿主 LLM 接管） |
| `kernel/guidance/extract.py` | 地点/研究者信息提取纯函数 | 迁移自 `_extract_locations_from_message`、`extract_researcher_info_from_message` |
| `kernel/guidance/informants.py` | 访谈对象类型库（结构化数据，初版人工 10-15 类：村落长老/宗族头人/宗教人士/基层干部/乡村教师/妇女群体/返乡务工者/集市商户/非遗传承人等），每类含接近策略、注意事项、建议提问域、伦理提示 | 新增（学术纵深） |
| `kernel/repository/` | `protocol.py`（`ProjectRepository` Protocol）、`json_repo.py`（承接 state.py）、`sqlite_repo.py`（里程碑2）。**kernel 能力函数不碰 repository**，落盘由宿主显式执行 | 拆自 `core/state.py`：状态读写归 repository，PHASES_ORDER/推进语义归 guidance |
| `kernel/models.py` | pydantic 数据契约：`FieldSiteProfile`、`ItineraryPlan`、`BudgetBreakdown`、`ResearchContext`、`ProposalDoc`、统一返回包络 | 新增 |
| `kernel/llm.py` | DashScopeChat 迁入 + function-calling 支持（OpenAI 兼容端点）+ 模型分档配置 | 迁移自 `tools/dashscope_tools.py` |

### 1.3 三条公理

1. **能力函数严格无状态**：入参→出参，不读写项目状态；副作用由宿主经 repository 显式执行。
2. **LLM 只出现在两处**：`documents/`（内核内）与宿主 agent loop（内核外）。搜索/聚合返回轻量 `summary_hints`（纯统计），不做解读文案。
3. **生长式迁移**：`kernel/` 与现有 `core/`、`tools/` 并存，逐能力迁移+测试转绿；`orchestrator` 改为 kernel 之上第一个宿主（CLI 变薄壳），最后删除旧决策逻辑。每步 CI 可过。

### 1.4 模型分档（用户指定）

```
FA_INTERVIEW_MODEL = qwen3.8-flash   # Web agent loop：访谈主持+function calling（高频、便宜）
FA_PROPOSAL_MODEL  = qwen-max        # compose_proposal / compose_interview_guide（低频、质量敏感）
FA_EXTRACT_MODEL   = qwen3.8-flash   # 结构化信息提炼小任务
```

现有 `Config.chat_model`/`system_model` 迁移时改名对齐三槽，env 可覆盖。

## 2. MCP Server 清单

官方 `mcp` Python SDK，stdio 传输；Streamable HTTP 留作后续可选。Server 是 kernel 薄壳：schema 翻译 + 参数校验 + repository 接线，零业务逻辑。repository 暴露为工具（MCP Server 作为宿主有权组合 kernel+repo；约束只针对 kernel 内部）。

### 2.1 Tools（21 个，四类）

**① 原子搜索（8 个，全部独立暴露）**：`search_flights`、`search_trains`、`search_hotels`、`search_pois`、`search_keyword`、`search_ai`、`search_marriott_hotels`、`search_marriott_packages`——与 sources 一一映射，只读；返回归一化 `items[]`。description 写明田野场景语义（宿主 LLM 靠 schema+description 选型，替代关键词路由）。

**② 聚合能力（2 个，只读不落盘）**：
- `profile_field_site(location, budget_per_night?, interests?, departure_city?, travel_date?, max_items_per_section?)` → `FieldSiteProfile`（默认截断防宿主上下文爆炸）；
- `compose_itinerary(departure, destinations[], start_date, duration_days, budget_total, researcher_count)` → `ItineraryPlan`。

**③ 文档能力（2 个，内部用 LLM）**：
- `compose_proposal(结构化 context 或 project_id, section?)` → Markdown；无 key 时模板拼接降级；
- `compose_interview_guide(research_topic, informant_types[], project_id?)` → 每类对象的半结构式提纲（开场/主问/追问/敏感问题处理/知情同意话术），供提案第三章引用。

**④ 状态与引导（9 个，Server 接线 repository/guidance）**：
`create_project`、`list_projects`、`get_project`、`update_research_context`、`save_field_site`、`save_informant`、`add_knowledge_entry`、`search_knowledge`（FTS5 版）、`check_phase_advance(project_id)` → `{can_advance, to_phase, missing_fields}`。

**阶段推进条件扩展**：B→C 需"有 confirmed 田野点 且 已登记 ≥1 个访谈对象"。

### 2.2 Resources

```
fieldwork://guidance/phases                  四阶段流程总览+推进条件
fieldwork://guidance/questions/{phase}       引导问题模板
fieldwork://guidance/checklists/{phase}      需采集字段清单
fieldwork://guidance/informants/library      访谈对象类型库
fieldwork://guidance/informants/{type}       单一类型档案模板
fieldwork://guidance/interview-guide/methodology  半结构式访谈方法论
fieldwork://templates/proposal-outline       八章 Proposal 模板
fieldwork://project/{id}/summary             项目快照
fieldwork://project/{id}/sites               已存田野点及画像
fieldwork://project/{id}/informants          已登记访谈对象清单
```

### 2.3 Prompts（MCP 第三原语）

`fieldwork_interview`（主持四阶段访谈，附资源引用）、`refine_proposal_section`。

### 2.4 统一约定

- 返回包络：`{ok, data?, error?: {code, message, hint}, degraded?}`；flyai 缺失返回 `ok:false, code:"flyai_unavailable"`，不抛异常。
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

取舍：LLM 选型完全交给 function calling（零关键词路由）；阶段推进判定确定性（`check_advance`），不由 LLM 宣布，防幻觉推进。

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

## 7. 边界与留作后续

- 向量化知识库检索（当前 FTS5 关键词/全文）；
- MCP Streamable HTTP 传输、多用户/鉴权、部署形态；
- DOCX/PDF/XLSX 导出与地图可视化（交付物专业化，作为后续独立设计）；
- 文献/已有研究检索接入学术纵深（方向 B 剩余部分，后续独立设计）；
- 钉钉同步保留现状，不迁移进 kernel 公共接口（宿主侧适配层，后续可择机移出 orchestrator）。
