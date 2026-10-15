# Fieldwork 前端（Plan 4/5）设计与实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为田野罗盘交付一套 React 前端（3 路由 / 三栏工作台 / 工具→卡片渲染器注册表），消费 Web 宿主（Plan 3）的 REST 快照 + SSE 事件流，把「四阶段访谈 + 实时飞猪数据 + 八章提案」可视化为学术档案气质的产品界面。

**Architecture:** 前后端分离；前端 = 无业务判定的**投影层**。所有状态来自两条通道：① REST（`GET /projects/{id}` 初始快照）② SSE（R4 两步：`POST /projects/{id}/messages` 建回合拿 turn_id → `GET /projects/{id}/turns/{tid}/events` 订阅回合内事件流）。中栏时间线与右栏状态面板**同源于 `tool_completed` payload**，数字不经 LLM 转述（spec §3.4/§4.3）。阶段推进是确定性事件（`phase_transition`），前端不做任何"是否可推进"的判断。

**Tech Stack:** Vite 5 + React 18 + TypeScript + Tailwind CSS（自定义 design tokens）+ zustand（stores）+ react-markdown/remark-gfm（提案与助手正文）+ 原生 `EventSource`/`fetch` 流式读取（SSE）。不引重型组件库（spec §4.1）。测试：Vitest（单元/组件）+ Playwright（L4 端到端，接 spec §6.L4 剧本）。

**Spec:** `docs/superpowers/specs/2026-10-14-fieldwork-agent-mcp-kernel-design.md` §4（前端全节）/§3.4（SSE 协议）/§3.5（部署）/§6.L4（Playwright 冒烟）。

**视觉设计板（唯一视觉事实源）:** `docs/frontend/design-board.html` —「学术档案·纸感温润」风格，含设计系统页 + 三屏高保真原型。本计划的 token 值与设计板严格同源，改视觉先改设计板。

**依赖前置（Plan 3 已落地，2026-10-15 实测契约）:** Web 宿主提供 `POST /projects`(201 `{project_id,phase}`)、`GET /projects`、`GET /projects/{id}`(快照)、`POST /projects/{id}/messages`(**返回 `{turn_id,status}` JSON，非 SSE**)、`GET /projects/{id}/turns/{tid}/events`(**SSE 流，支持 Last-Event-ID 回放**)、`POST /projects/{id}/turns/{tid}/cancel`、`GET /projects/{id}/proposal`、`GET /projects/{id}/proposal/download`。R4 回合协议：创建与订阅**分离**（POST 建回合拿 turn_id → GET 订阅事件流），单项目单写回合（占用中 POST 返回 409）。开发期前端可先对 mock 服务器联调（Task 2）。

## Global Constraints

- **投影层无业务逻辑**：前端不判断阶段能否推进、不做预算超支计算、不改写工具返回的数字。这些都是 kernel/宿主职责；前端只渲染 payload + 应用纯视觉规则（如 `over_budget===true` → 红色条）。
- **单一数据源**：中栏工具结果卡与右栏面板读同一份 `tools[call_id]` 记录（spec §4.3 原则）。禁止两处各自解析 SSE。
- **右栏不自动强切 Tab**：阶段推进/新数据到达时发切换建议 toast，用户点确认才切（spec §4.3，已确认）。
- **界面禁 emoji**：图标一律内联线形 SVG（1.5px 描边，20px，`inksoft`），延续用户既有偏好与设计板图标库。
- **数字排版**：报价/统计用衬线等宽（`Fraunces` / `num` class），右对齐，标注来源徽标（search/cheapest/estimate/manual），呼应"可复核可溯源"。
- **降级可见**：工具失败不隐藏，`ToolActivityStrip` 灰显 + 降级 chip（`code`），`error` 事件 → 顶部降级横幅（spec §3.4）。
- **构建产物即静态站**：`vite build` → `web/dist`，由 FastAPI 挂载（spec §3.5）；开发期 Vite proxy 转发 `/projects` 到 `:8300`。
- 标识符英文、注释中文；组件 PascalCase、store/hook camelCase；行宽随 Prettier 100。

## 设计系统 Tokens（与设计板同源）

```
色板（Tailwind theme.extend.colors）— 已按"久读减负"减淡：背景趋近暖白、降饱和
  paper      #F9F6F0   页面底（暖白纸，非米黄）
  paperdeep  #F1ECE2   左/右栏底
  card       #FDFCF9   卡片面（近白，最大化正文可读性）
  cardsunk   #F4F0E7   内嵌/代码/内凹
  ink        #2A2724   主文字/标题（墨黑，微降纯黑减刺激）
  inksoft    #5F584C   次级正文
  inkfaint   #948B7A   弱文字/占位/元信息
  hairline   #E2D9C8   描边/分隔线
  hairsoft   #EBE5D9   更淡分隔
  cinnabar   #A8412E   朱砂 · 品牌主色/进行中/强调
  cinnabarsoft #C9705C 朱砂浅
  cinnabarwash #F6EBE6 朱砂淡底（badge/横幅，减淡）
  indigo     #3A5C74   靛蓝 · 数据/链接/信息（提亮）
  indigowash #E9EEF1
  bamboo     #5E7A4E   竹青 · 完成/确认/预算内
  bamboowash #EBF0E5
  ochre      #A8802C   赭黄 · 警告/缓冲日
  ochrewash  #F5EFDF
  danger     #9E2F26   超支/错误红警

字体（fontFamily）— 全局单一宋体族，无衬线仅数字场景不出现
  serif  "Noto Serif SC","Songti SC","STSong","SimSun",serif   标题/正文/UI（默认族）
  sans   同 serif（Tailwind font-sans 亦映射宋体，杜绝混排无衬线）
  num    "Fraunces","Noto Serif SC","Songti SC",serif           数字/报价（lining-nums）
  补偿：宋体小字号笔画细，UI 元信息 ≥11px + medium 字重 + lh1.7；body 用 -webkit-font-smoothing:auto 避免过细

圆角      r-sm 4 / r-md 8 / r-lg 14（档案感偏方正，分隔优先 1px 界线而非阴影）
阴影      shadow-card（浮起卡片）/ shadow-pop（toast、悬浮层）；整体弱阴影
间距      4 8 12 16 24 32 48（Tailwind spacing 默认即可）
纸纹      body 叠加极低对比 SVG feTurbulence 噪点 + 双角向径向淡色（见设计板 <style>）

状态语义色映射
  阶段完成 bamboo · 进行中 cinnabar · 未解锁/待办 hairline+inkfaint
  预算内 bamboo · 超支 danger · 缓冲日/降级 ochre · 数据/链接 indigo
```

## 品牌资产（已定案）

- **主标识 = 概念 B「田·罗盘」**（纯几何：圆环+田字方框+十字+东北朱砂象限+中心点，无文字依赖）。
  生产资产在 `docs/frontend/logo/`：`fieldwork-logo-{full,mono,reverse,cinnabar}.svg`、`favicon.svg`、`favicon-16.png`、`favicon-32.png`、`apple-touch-icon.png`(180)、`logo-512.png`；`build_logo.py` 可复跑重生成。
  用法：顶栏品牌位、工作台左栏、移动端 app bar、favicon/apple-touch-icon。字标（田野罗盘 / FIELDWORK COMPASS）用**活字**（Noto Serif SC + 字距）随 lockup 排版，不做转曲 SVG。
- **辅助印记 = 妙谧智能既有 M 印**（公司品牌资产，源 `E:\HTML_Projects\Miovelle\assets\mark-cinnabar.svg`，已拷入仓库 `docs/frontend/logo/miovelle-mark-cinnabar.svg`；朱砂 #A8412F 与本品朱砂同族）。
  用法：助手头像、提案落款、加载页等"出品方"署名位。Task 1 拷入 `web/public/`。**不另造 C 罗印章**（已弃用）。
- 设计板内联引用：`#lg-b`（主标）、`#lg-mio`（M 印）两个 symbol。

## 文件结构

```
web/
├── index.html
├── public/             品牌资产：fieldwork-logo-*.svg、favicon.svg/png、apple-touch-icon.png、mark-cinnabar.svg(M印)
├── package.json  vite.config.ts  tsconfig.json  tailwind.config.ts  postcss.config.js
├── src/
│   ├── main.tsx            React 挂载 + RouterProvider
│   ├── App.tsx             路由出口 + 全局降级横幅挂载点
│   ├── styles/tokens.css   @tailwind + 纸纹/滚动条/seal 等自定义层
│   ├── api/
│   │   ├── rest.ts         projects CRUD + proposal 拉取（fetch 封装，统一错误）
│   │   └── sse.ts          SSE 客户端：连接/last-event-id 重连/事件解析→回调
│   ├── types/
│   │   ├── events.ts       SSE 事件 TS 类型（对齐 spec §3.4 六事件 + 内核 Envelope）
│   │   └── models.ts       FieldSiteProfile/ItineraryPlan/ResearchContext 等前端镜像类型
│   ├── stores/
│   │   ├── project.ts      项目快照 + phase（REST 初始化，phase_transition 更新）
│   │   ├── conversation.ts 消息时间线 + 流式 token 缓冲 + 回合状态
│   │   ├── tools.ts        call_id → {status,args,purpose,ok,data,degraded} 索引
│   │   └── ui.ts           右栏 activeTab、切换建议 toast、降级横幅、折叠态
│   ├── hooks/
│   │   ├── useTurn.ts      发起一回合：POST messages → 驱动 sse → 写三 store
│   │   └── useSSE.ts       封装 sse.ts，绑定 last-event-id 断线补发
│   ├── components/
│   │   ├── layout/         AppShell、PhaseRail(左)、ContextPanel(右)、TopBar
│   │   ├── chat/           MessageList、UserBubble、AssistantMarkdown、Composer、PhaseBanner
│   │   ├── tools/          ToolActivityStrip + 渲染器注册表(见下) + ToolResultCard(折叠壳)
│   │   ├── context/        ProgressChecklist、SiteProfilePanel、InformantTable、BudgetBar、KnowledgeSummary、SwitchSuggestToast
│   │   ├── proposal/       ProposalViewer、ChapterNav、VersionSwitch、InterviewGuideView
│   │   └── ui/             Badge/Chip、Button、Icon(线形SVG集)、Rule、Seal、Card
│   ├── registry/
│   │   └── toolCards.ts    toolName → 卡片组件 的注册表（新增工具只在此加一行）
│   ├── routes/
│   │   ├── ProjectHub.tsx      /
│   │   ├── Workspace.tsx       /p/:id
│   │   └── ProposalView.tsx    /p/:id/proposal
│   └── lib/                formatYuan、priceSourceLabel、markdown 组件映射、cn()
├── mock/
│   └── sseServer.ts        开发期 mock：POST /messages 返回 {turn_id}，GET /turns/:tid/events 脚本化回放六事件（含降级/超支/推进），无后端可跑
└── tests/
    ├── stores.spec.ts      三 store reducer：SSE 事件序列 → 期望状态
    ├── toolCards.spec.tsx  每卡片对代表性 payload 渲染快照（价格不失真断言）
    └── e2e/fieldwork.spec.ts  Playwright L4 剧本（对齐 spec §6.L4）
```

## SSE 事件 → 前端消费映射（spec §3.4 + Plan 3 EventEnvelope 实测）

**信封形状（Plan 3 实测）**：每条 SSE 帧 `id:` = 单调 seq，`event:` = 事件名，`data:` = `{turn_id, seq, ...payload}`（payload 字段见下表）。前端解析后按 `event:` 分派，`data.seq` 用于去重与 Last-Event-ID 重连锚点。

| event | payload（data 内除 turn_id/seq 外的字段） | 前端处理 |
|-------|---------|---------|
| `message_delta` | `{text}` | `conversation` 追加到当前助手消息缓冲，打字机渲染 |
| `tool_started` | `{call_id,tool,args,purpose}` | `tools[call_id]={status:'running',args,purpose}`；中栏 `ToolActivityStrip` 加脉冲点；右栏对应面板可亮"更新中"角标（不切 Tab） |
| `tool_completed` | `{call_id,ok,data,degraded?}` | `tools[call_id]` 落 `status:'done'/'failed'+data`；中栏渲染 `toolCards[tool]` 卡片；右栏面板订阅同源 `data` 更新；`degraded` → ochre 降级 chip |
| `phase_transition` | `{from,to}` | `project.phase=to`；左栏 `PhaseRail` 进度动画；中栏 `PhaseBanner`；`ui` 发切换建议 toast（不自动切） |
| `turn_done` | `{reply_id,usage}` | `conversation` 收尾定格；Composer 解锁；记录 token 用量 |
| `error` | `{code,message,hint}` | `ui.degradedBanner` 顶部降级横幅；不中断已渲染内容 |

**断线重连**：`sse.ts` 维护 `lastEventId`（= 已收最大 `seq`），断开后对 `GET /projects/{id}/turns/{tid}/events` 带 `Last-Event-ID` 头重连，宿主从该 seq 之后补发（Plan 3 `TurnManager.replay` 已实现，回合已结束则回放全部后立即关闭）。前端事件处理需**幂等**（同 `call_id` 重复 `tool_completed` 覆盖而非追加；按 `seq` 去重）。

## 工具→卡片渲染器注册表（spec §4.4）

`registry/toolCards.ts` 是"新增工具只加组件"的落点。key = kernel 能力名，value = 卡片组件。未在表内的工具走通用 `GenericJsonCard`（折叠 JSON，兜底不崩）。

```
search_flights / search_trains        → TransportCard     （图标区分航班/高铁，价格 num，降级灰显）
search_hotels / search_marriott_*     → HotelCard         （缩略图占位+名+¥/晚+来源）
search_pois                           → POICard           （文化资源 chip 网格）
search_keyword / search_ai            → KeywordResultList （轻量列表，含 ai 语义意图标注）
profile_field_site                    → SiteProfilePanel  （交通/住宿/文化/配套四段 + summary_hints）
compose_itinerary                     → ItineraryTimeline （竖向：适应/深入/补充期 + 缓冲日 ochre + 备选折叠）+ BudgetBar
compose_proposal                      → ProposalViewer    （八章导航 + 正文 + 版本 + 下载.md）
compose_interview_guide               → InterviewGuideView（按对象类型分节：开场/主问/追问/敏感/知情同意）
create/list/get_project, update_*,    → 无卡（状态类，只更新右栏面板 + toast）
save_field_site/informant, add/search_knowledge, check_phase_advance
```

`ToolResultCard` 是统一折叠壳：header（工具名 + 状态徽标 + 降级 chip）+ body（注册表选出的卡片）。中栏默认折叠、可展开；右栏面板复用同一卡片组件（同源 data）。

## 三路由屏幕规格（细节见设计板）

**① `/` ProjectHub**：顶部品牌 + 「新建项目」；新建卡（学科倾向 chips：人类学/社会学/民族学/公共卫生/教育学/建筑乡建；快速模板 chips；一句话描述输入 → `POST /projects`）；项目卡网格（名 + 阶段徽标 + 学科/规模/预算 + 田野点/访谈对象计数 + 更新时间 + 四段进度条）。侧栏放"四阶段访谈"与"学术纵深（访谈对象库）"说明。

**② `/p/:id` Workspace 三栏**：
- 左 `PhaseRail`：A/B/C/D 阶段项（完成 bamboo 勾 / 进行 cinnabar / 未解锁 lock 灰）+ 每阶段副标（如"2 点·1 已确认"）+ 底部"返回项目集"。
- 中 时间线：`PhaseBanner`（阶段横幅）→ 用户气泡（indigo）→ `ToolActivityStrip`（活动/降级）→ `AssistantMarkdown`（流式，宋体）→ `ToolResultCard`（折叠行程/画像卡）→ `Composer`（textarea + 发送 + 取消回合）。
- 右 `ContextPanel`：Tab（进度/田野点/访谈对象/预算/知识库），**手动切换**；`SwitchSuggestToast` 浮于顶部给建议。各 Tab = 对应卡片组件投影同源 data。

**③ `/p/:id/proposal` ProposalView**：左八章导航（`ChapterNav`，滚动高亮）+ 版本切换（draft vN / final）+ kind 切换（proposal / interview_guide）+ 下载 .md；正文宋体纸面阅读排版（标题带编号、预算表、伦理话术引用块）。数据来自 `GET /projects/{id}/proposal`。

## 移动端响应式方案（细节见设计板视图④）

同一批组件 + 卡片渲染器，仅**投影容器**随断点切换，不重写业务组件。断点 `sm 640 / md 768 / lg 1024`；`<lg` 折叠为单列，`≥lg` 恢复三栏。

| 桌面 | 移动端 | 交互要点 |
|------|--------|---------|
| 左栏 `PhaseRail` | 顶部横向 stepper（A✓ B✓ C● D🔒） | 恒显、可点按跳章；进行中朱砂 ring |
| 右栏 `ContextPanel` | 底部抽屉 bottom sheet | 悬浮按钮/stepper 唤起；下拉 grabber 收；遮罩压暗对话 |
| `SwitchSuggestToast` | 抽屉入口红点 + 抽屉内横幅 | **仍不自动切 Tab**，点「去查看」才切 |
| 工具结果卡（中栏） | 默认折叠 | 点开看详情，省纵向滚动 |
| 八章 `ChapterNav`（左） | 顶部横滑 chips + 当前章高亮 | 版本收进右上 chip |
| Composer / 下载栏 | 固定底部 + `env(safe-area-inset-bottom)` | 避开键盘与手势条 |

新增移动端专属：ProjectHub 底部 Tab 栏（项目/知识库/我的）——为移动端导航提案，桌面端不显示，需在 Plan 4 实现时与用户确认是否保留。

`ContextPanel` 实现为「容器自适应」组件：`useMediaQuery('(min-width:1024px)')` 为真渲染右栏、为假渲染 bottom sheet，内部 Tab 与卡片组件完全复用。

## 任务拆分

### Task 1: 工程脚手架 + 设计 tokens
**Files:** Create `web/`（package.json、vite.config.ts、tailwind.config.ts、tsconfig、postcss、index.html、src/main.tsx、src/App.tsx、src/styles/tokens.css）、`web/tests/` 空骨架。
- [ ] **Step 1:** `npm create vite@latest web -- --template react-ts`；装 `tailwindcss postcss autoprefixer zustand react-router-dom react-markdown remark-gfm`；dev 装 `vitest @testing-library/react @playwright/test jsdom`。
- [ ] **Step 2:** `tailwind.config.ts` 写入本计划「设计系统 Tokens」全部 color/font/boxShadow；`tokens.css` 迁移设计板 `<style>` 的纸纹 body、`.num`、`.hairline`、`.scroll` 滚动条、`.chip`（**不迁移 `.seal`**——C 罗印章已弃用）。
- [ ] **Step 2b:** 拷贝品牌资产到 `web/public/`：`docs/frontend/logo/fieldwork-logo-*.svg`、`favicon.svg`、`favicon-16/32.png`、`apple-touch-icon.png`、`logo-512.png`、`miovelle-mark-cinnabar.svg`（M 印，供头像/落款引用）。
- [ ] **Step 3:** `vite.config.ts` 配 `server.proxy['/projects'] → http://localhost:8300`、`build.outDir='dist'`；`App.tsx` 建三路由占位。
- [ ] **Step 4:** 起 dev，访问 `/` 见米白纸底 + 宋体标题即通过；`vitest` 空跑通过。
- [ ] **Step 5:** Commit `feat(web): 前端脚手架与学术档案设计tokens`。

### Task 2: 类型 + mock SSE 服务器（无后端可联调）
**Files:** Create `src/types/events.ts`、`src/types/models.ts`、`mock/sseServer.ts`。
**Interfaces:** events.ts 导出六事件 discriminated union（`type SseEvent = MessageDelta|ToolStarted|ToolCompleted|PhaseTransition|TurnDone|ErrorEvent`，各事件 payload 字段对齐 spec §3.4）**+ 统一信封 `type EventEnvelope = {turn_id:string; seq:number; event:string; data:Record<string,unknown>}`**（对齐 Plan 3 `web_host/events.py`）；models.ts 镜像内核 `FieldSiteProfile/ItemPage/SearchItem/ItineraryPlan/BudgetBreakdown/ResearchContext/InformantRecord/ProposalDoc/Envelope`（从 Plan 1 `kernel/models.py` 逐字段翻译，价格 `number|null`）。
- [ ] **Step 1:** 写类型；`sseServer.ts` 用 Node http 起 `:8301`，**两步式对齐 Plan 3**：`POST /projects/:id/messages` 返回 `{turn_id,status}` JSON 并把脚本存内存；`GET /projects/:id/turns/:tid/events` 返回 `text/event-stream`，脚本化回放一段完整回合（含 message_delta 分片、两个 tool_started/completed、一个 `degraded:flyai_unavailable`、phase_transition、turn_done），每帧写 `id:`(seq)+`event:`+`data:{turn_id,seq,...}`，支持 Last-Event-ID 从断点补发；`POST /projects/:id/turns/:tid/cancel` 返回终态。
- [ ] **Step 2:** `curl -N` 手测：先 POST 拿 turn_id，再 GET 事件流逐条打印、断连重连带 Last-Event-ID 能从断点续。
- [ ] **Step 3:** Commit `feat(web): SSE事件类型与mock回放服务器`。

### Task 3: stores（三 store reducer，纯函数可测）
**Files:** Create `src/stores/{project,conversation,tools,ui}.ts`、`tests/stores.spec.ts`。
**Interfaces:** `tools.applyEvent(store, event)` 幂等更新 `call_id` 索引；`conversation.applyEvent` 处理 delta 追加与 turn_done 定格；`project.applyEvent` 处理 phase_transition；`ui` 管 activeTab/toast/degradedBanner（**不**因事件自动改 activeTab，只置 pendingSuggestion）。
- [ ] **Step 1: 写失败测试** — 给定事件序列 `[tool_started(c1),tool_completed(c1,ok,data),tool_completed(c1,同 data 重复),message_delta×3,turn_done]`，断言 `tools` 只有 1 条 c1（幂等）、`conversation` 助手文本 = 3 段拼接、回合 done；给 `[phase_transition(B,C)]` 断言 `project.phase==='C'` 且 `ui.activeTab` **不变**、`ui.pendingSuggestion` 置位。
- [ ] **Step 2:** 确认失败 → **Step 3:** 实现 zustand store + applyEvent → **Step 4:** 通过 → **Step 5:** Commit `feat(web): 三store事件归约(幂等+右栏不自动切)`。

### Task 4: SSE 客户端 + useTurn 接线
**Files:** Create `src/api/{rest.ts,sse.ts}`、`src/hooks/{useSSE.ts,useTurn.ts}`。
- [ ] **Step 1:** `sse.ts` 用 `fetch` + `ReadableStream` **GET** `/projects/{id}/turns/{tid}/events` 解析 `event:/data:/id:`（R4：GET 订阅，原生 EventSource 亦可，但手写解析便于带自定义头与统一去重），维护 lastEventId(=max seq)、指数退避重连、心跳超时（宿主每 15s 发 `: heartbeat` 注释帧，需忽略）。
- [ ] **Step 2:** `useTurn(projectId, text)`：**两步式**——① `POST /projects/{id}/messages` 拿 `{turn_id}`（409 表示已有回合在跑，提示用户）；② 用 turn_id 起 `sse.ts` GET 订阅 → 逐事件按 `seq` 去重后 dispatch 到三 store applyEvent；暴露 `cancel()` 调 `POST /projects/{id}/turns/{turn_id}/cancel`。
- [ ] **Step 3:** 对 mock 服务器联调：控制台见事件流正确落入 store；断网重连补发不产生重复卡。
- [ ] **Step 4:** Commit `feat(web): SSE客户端与回合hook(断线补发+取消)`。

### Task 5: UI 原语 + 布局骨架
**Files:** Create `src/components/ui/*`（Button、Badge/Chip、Icon 线形集、Rule、Seal、Card）、`src/components/layout/*`（AppShell、TopBar、PhaseRail、ContextPanel 空壳）。
- [ ] **Step 1:** 从设计板抽 Icon 符号（compass/pin/plane/train/bed/temple/wallet/users/book/check/lock/alert/spark/download/caret/send/plus/clock）为 `<Icon name=.. />` 组件。
- [ ] **Step 2:** Badge/Chip 实现阶段态与状态语义色映射（bamboo/cinnabar/hairline/ochre/danger）。
- [ ] **Step 3:** Workspace 三栏栅格 `grid-cols-[212px_1fr_340px]`，高度 `calc(100vh-…)`，各栏内部滚动；`<lg` 断点折叠为单列时间线，`ContextPanel` 经 `useMediaQuery` 在右栏/底部抽屉两形态间切换（复用同批卡片，见「移动端响应式方案」）。
- [ ] **Step 4:** 与设计板逐屏比对（间距/描边/圆角/字号）校准，含设计板视图④四张移动端原型。
- [ ] **Step 5:** Commit `feat(web): UI原语与三栏布局骨架`。

### Task 6: 工具卡片渲染器注册表 + 各卡片
**Files:** Create `src/components/tools/*`、`src/registry/toolCards.ts`、`tests/toolCards.spec.tsx`。
- [ ] **Step 1: 写失败测试** — 对 `TransportCard/HotelCard/POICard/SiteProfilePanel/ItineraryTimeline/BudgetBar/InformantTable/ToolActivityStrip` 各喂一份代表性 payload，断言渲染出的价格文本与 payload **逐字相等**（防失真，呼应 spec §3.4"数字不经 LLM 转述"）；断言 `over_budget:true` → 条为 danger 色；断言未注册工具落 `GenericJsonCard` 不崩。
- [ ] **Step 2:** 确认失败 → **Step 3:** 实现 `ToolResultCard` 折叠壳 + 注册表 + 各卡片（视觉照设计板）→ **Step 4:** 通过 → **Step 5:** Commit `feat(web): 工具卡片注册表与结果卡(价格不失真)`。

### Task 7: 中栏对话流 + 右栏面板接线
**Files:** Create `src/components/chat/*`、`src/components/context/*`；接线 Workspace。
- [ ] **Step 1:** `MessageList` 消费 conversation store；`AssistantMarkdown` 用 react-markdown+remark-gfm，宋体排版；流式光标。
- [ ] **Step 2:** `ToolActivityStrip` 消费 tools store（running 脉冲 / done / failed 灰显 + 降级 chip）。
- [ ] **Step 3:** 右栏 Tab 各面板复用 Task 6 卡片组件投影同源 data；`SwitchSuggestToast` 读 `ui.pendingSuggestion`，点确认才 `setActiveTab`。
- [ ] **Step 4:** `Composer` 发送 + 回合中禁用 + 取消按钮；`PhaseBanner` 读 phase_transition。
- [ ] **Step 5:** Commit `feat(web): 对话时间线与右栏状态投影(同源payload)`。

### Task 8: ProjectHub + ProposalView
**Files:** Create/接线 `src/routes/ProjectHub.tsx`、`ProposalView.tsx`、`src/components/proposal/*`。
- [ ] **Step 1:** ProjectHub：`GET /projects` 列表 + 新建卡（学科/模板 chips + 描述）→ `POST /projects` → 跳 `/p/:id`。
- [ ] **Step 2:** ProposalView：`GET /projects/{id}/proposal` → `ProposalViewer` 八章 `ChapterNav`（滚动高亮）+ 版本/kind 切换 + `下载 .md`（blob）。
- [ ] **Step 3:** 提案正文宋体纸面排版：章节编号标题、预算表、伦理话术引用块（照设计板）。
- [ ] **Step 4:** Commit `feat(web): 项目集与提案阅读/导出`。

### Task 9: L4 端到端冒烟（Playwright，对齐 spec §6.L4）
**Files:** Create `web/tests/e2e/fieldwork.spec.ts`、`web/playwright.config.ts`；CI 挂载。
- [ ] **Step 1:** 剧本：新建项目 → 阶段 A 输入研究描述 → 右栏采集清单逐项打勾 → 推进阶段 B → 断言田野点卡片价格与 mock payload 一致 → generate → 八章可见 → 下载 .md 触发。后端用 fake flyai stub + 录制回放 fake LLM（无 key）。
- [ ] **Step 2:** `playwright install`；本地跑绿；**L4 不允许静默 skip**（spec §6.CI）。
- [ ] **Step 3:** 接入 `.github/workflows`：现有矩阵加 Node 安装 + `npm ci && npm run test:e2e`。
- [ ] **Step 4:** Commit `test(web): Playwright四阶段端到端冒烟(CI门禁)`。

### Task 10: 构建挂载 + 文档
**Files:** Modify `web_host/app.py`（挂 `web/dist` 静态）、`README.md`（前端启动/构建段）、`DEVELOPMENT.md`（前端扩展点：新增工具卡片只改 `registry/toolCards.ts`）。
- [ ] **Step 1:** FastAPI `StaticFiles` 挂 `web/dist` 到 `/`（API 路由优先）；`vite build` 产物就位。
- [ ] **Step 2:** README 加「Web 前端」段：`cd web && npm i && npm run dev`（proxy :8300）/ `npm run build`；mock 联调 `npm run mock`。
- [ ] **Step 3:** DEVELOPMENT.md §5 扩展点补前端条目；标注视觉事实源 = `docs/frontend/design-board.html`。
- [ ] **Step 4:** Commit `feat(web): 构建产物挂载Web宿主与文档`。

## 验收（整盘）

1. `npm run build` 通过；FastAPI 挂载后 `GET /` 返回 React 应用，三路由可达。
2. `npm run test`（Vitest）全绿：stores 幂等/右栏不自动切、卡片价格不失真断言通过。
3. `npm run test:e2e`（Playwright L4）在 CI 跑绿，覆盖 spec §6.L4 完整剧本，无静默 skip。
4. 无后端时用 `npm run mock` 可完整走查一回合（含降级/推进/超支态），界面与设计板逐屏一致。
5. 视觉核对：token 值、组件形态与 `docs/frontend/design-board.html` 同源；**全局单一宋体族**（`font-sans` 亦映射宋体，界面无系统无衬线混排）；界面零 emoji、图标全线形 SVG、数字带来源徽标。
6. 移动端核对：视口 `<lg` 时 Workspace 折叠为单列时间线 + 底部抽屉、stepper 恒显、切换建议不自动切 Tab；与设计板视图④四张原型逐屏一致。
7. 新增一个 kernel 工具时，前端只需在 `registry/toolCards.ts` 加一行映射 + 一个卡片组件即可渲染（扩展点验证）。
```
