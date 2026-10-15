# 田野罗盘 (Fieldwork Agent)

> 面向人类学/社会学田野调查者的 AI 旅行规划助手  
> 基于飞猪 FlyAI 全品类旅行搜索能力，引导式访谈 → 自动生成可提交的研究方案（Research Proposal）

## 一句话介绍

**田野罗盘** — 帮田野调查者从"想去哪里"到"怎么抵达、住在哪里、调研什么"，一站式生成完整研究方案。

## 架构总览

```
┌─────────────────────────────────────────────┐
│         CLI 交互界面 (core/cli.py)            │
│       prompt_toolkit 驱动的交互式终端           │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│     FieldAgent 内核宿主 (core/orchestrator)   │
│  · 对话生命周期 + LLM function calling 决策    │
│  · 取 registry schema → client_kernel_dispatch│
│  · 轮末落盘 + check_advance 确定性推进         │
│  · Config (core/config.py) 为无状态底座        │
└──┬────────────────┬───────────────┬─────────┘
   │                │               │
┌──▼──────────┐ ┌───▼────────────┐ ┌▼─────────────────┐
│ runtime/    │ │ kernel/        │ │ kernel/          │
│ loop.py     │ │ sources/       │ │ guidance/        │
│ 共用回合循环 │ │  flyai 8 搜索原语│ │ documents/       │
│ LLM ↔ 工具   │ │ aggregation/   │ │ 五阶段推进·提取 ·  │
│             │ │ 画像/行程聚合    │ │ 方案/提纲/候选生成  │
└─────────────┘ └───┬────────────┘ └──────────────────┘
                    │
┌───────────────────▼─────────────────────────┐
│  kernel/registry.py + kernel/models.py       │
│  · 能力注册表：build_tool_schemas 单一来源     │
│  · 统一包络 Envelope/ToolError + 共享契约模型   │
├─────────────────────────────────────────────┤
│  kernel/repository/  (JsonRepository)        │
│  · 本地 JSON 项目状态 · 知识库 · 搜索缓存 · 方案 │
└─────────────────────────────────────────────┘
```

`kernel` 无状态、只依赖标准库 + pydantic，绝不反向 import `core/`/`runtime`；
`kernel/llm.py` 提供 DashScope OpenAI 兼容端点的 `LLMClient`（function calling），由宿主注入。

## 快速开始

### 安装依赖

```bash
# Python 依赖
pip install -U dashscope prompt-toolkit

# FlyAI CLI（飞猪旅行搜索能力）
npm i -g @fly-ai/flyai-cli

# 安装 Skill
npx skills add alibaba-flyai/flyai-skill -g -y --agent '*'
```

### 配置密钥

```bash
# 百炼 API Key（用于 LLM 对话）
export DASHSCOPE_API_KEY="sk-xxx"

# FlyAI API Key（自动从 ~/.flyai/config.json 读取，无需额外配置）
```

### 启动

```bash
cd fieldwork_agent
python -m fieldwork_agent
```

### 操作流程示例

```
fieldwork > new my_research
✅ 新项目已创建: my_research

fieldwork [my_research] (📍 研究定位) > 
我是社会学博士生，想研究西南少数民族村落的宗族组织变迁。
单人调研，计划3个月驻村观察。经费来自国家社科基金青年项目，预算2万元。
时间窗口是明年6-9月。

💡 引导问题:
  • 您对田野地点的确定程度如何？（已明确地点 / 有区域范围 / 希望我推荐）
```

## Web 宿主（FastAPI）

```bash
pip install -e ".[dev]"
fieldwork-web                    # 启动在 http://127.0.0.1:8300
```

开发期前端（Plan 4）通过 Vite proxy 转发 `/projects` 到 `:8300`。

端点概览：
- `POST /projects` — 创建项目
- `GET /projects/{id}` — 项目快照
- `POST /projects/{id}/messages` — 创建回合（返回 turn_id）
- `GET /projects/{id}/turns/{tid}/events` — SSE 事件流
- `POST /projects/{id}/turns/{tid}/cancel` — 取消回合
- `GET /projects/{id}/proposal` — 最新提案
- `GET /projects/{id}/proposal/download` — 下载 .md

## Web 前端

前端在 `web/`（Vite + React + TypeScript + Tailwind），消费 Web 宿主的 REST + SSE。

### 开发

```powershell
cd web
npm install
npm run dev          # vite dev :5173，proxy /projects → :8300
```

另起 Web 宿主：`fieldwork-web`（:8300）。无后端时可用 mock 联调：`npm run mock`（:8301，两步式 SSE 回放）。

### 构建与挂载

```powershell
cd web && npm run build   # 产物 → web/dist
```

`web/dist` 存在时，Web 宿主（`fieldwork-web`）自动挂载到 `/`（API 路由优先，SPA 深链 fallback）。访问 `http://127.0.0.1:8300/`。

### 测试

```powershell
cd web
npm run test         # vitest 单元/组件
npm run test:e2e     # Playwright L4 端到端（首次需 npx playwright install）
```

## 🎯 飞猪 FlyAI 全部 8 个工具深度集成

| 工具 | 田野调查场景 | 调用策略 |
|------|------------|---------|
| `search-flight` | 跨省调研大交通 | 按价格排序筛选预算内选项 |
| `search-train` | 高铁备选方案 | 时长优先排序 |
| `search-hotel` | 住宿搜索（民宿/客栈优先） | 多策略：民宿→经济酒店→距离筛选 |
| `search-poi` | 文化场所检索（30+分类） | 人文古迹/宗教场所/古镇古村/博物馆并行 |
| `keyword-search` | 广义覆盖（签证/保险/租车等） | 任何阶段兜底搜索 |
| `ai-search` ⭐ | **AI语义理解复杂意图** | 核心杀手锏——自然语言描述行程需求 |
| `marriott-hotel` | 高端酒店备选 | 团队调研或接待专家时推荐 |
| `marriott-package` | 体验套餐/下午茶 | 邀请同行学者时的接待安排 |

> **覆盖范围（实测 2026-10-14）**：机票明确支持国内+国际，酒店/POI/语义搜索覆盖海外主流目的地与港澳台（曼谷/清迈/加德满都/香港均验通）；火车限境内+港澳直通（香港西九龙）。海外偏远田野点（如巴新高地）无村级住宿供给，Agent 会如实声明并给出"枢纽城市+当地学术接待"二段式方案。

## 四阶段引导式访谈流程

### 📍 阶段 A：研究定位
收集学科背景、研究问题、田野点确定程度、调研规模、经费与时间窗口。

### 🗺️ 阶段 B：田野点画像
对候选地点全面采集：交通可达性、住宿条件、文化资源、生活配套。
Agent 会同时调用多个 FlyAI 工具获取真实数据。

### 🗓️ 阶段 C：行程规划
综合航班+火车+住宿+文化景点，对比最优路线，设计调研节奏和缓冲期。

### 📝 阶段 D：方案生成
整合全部信息，生成结构完整的研究方案（Research Proposal）Markdown 草稿。
当前输出为通用八章体例（适用海外/港澳申请与课程场景）；高校开题、国家社科活页体例在规划中。
行程、预算、文化场所均基于实时搜索数据并标注来源与完整性；缺数据时如实标记待核验，不虚构参考文献。

## 命令参考

| 命令 | 说明 |
|------|------|
| `new [name]` | 创建新项目 |
| `list` | 列出所有项目 |
| `open <id>` | 打开已有项目 |
| `chat <消息>` | 发送对话消息 |
| `generate` | 生成完整研究方案 |
| `knowledge add <标题>` | 添加自有资料到知识库 |
| `knowledge list` | 查看知识库内容 |
| `status` | 查看项目状态 |
| `sync dingtalk` | 同步方案到钉钉（可选） |
| `help` | 显示帮助 |

## 目录结构

```
fieldwork_agent/
├── core/                    # 宿主层：编排与状态，不含业务规则
│   ├── config.py            # 全局配置（本地优先，自动读取 flyai CLI 配置）
│   ├── orchestrator.py      # FieldAgent 内核宿主：组装 + 分派 + 落盘 + 确定性推进
│   └── cli.py               # 交互式终端界面
├── runtime/                 # 共用回合循环（CLI/Web 同一套 LLM↔工具多轮逻辑）
│   └── loop.py              # run_tool_loop
├── kernel/                  # 无状态能力内核（只依赖标准库 + pydantic）
│   ├── __init__.py          # 聚合导入各能力模块：裸 import kernel 即注册 13 项能力
│   ├── registry.py          # 能力注册表 + build_tool_schemas（工具 schema 单一来源）
│   ├── models.py            # 共享契约：Envelope/ToolError/SearchItem/ResearchContext…
│   ├── llm.py               # LLMClient（DashScope OpenAI 兼容端点，function calling）
│   ├── sources/             # flyai.py：FlyAIClient + 8 搜索原语（argv 拼装 → 归一化包络）
│   ├── aggregation/         # site_profile.py 田野点画像 · itinerary.py 行程组合
│   ├── documents/           # proposal.py 八章方案与访谈提纲 · informants.py 候选生成
│   ├── guidance/            # phases.py 五阶段与 check_advance · extract.py · informants.py
│   └── repository/          # protocol.py 契约 · json_repo.py JsonRepository 持久化
├── tests/                   # 全 mock 测试（无 Key、不联网、不起子进程）
├── __init__.py
├── __main__.py              # 入口点
└── README.md
```

## 比赛适配

本作品针对 OPC 飞猪旅行智能体大赛设计：

- **创新性 40%** — 将飞猪 8 个旅行搜索工具创造性应用于学术田野调查场景，ai-search 作为核心差异化能力处理自然语言复杂意图
- **完整度 30%** — 四阶段访谈全流程跑通，每个阶段都触发真实的 FlyAI 工具调用，输出稳定可验证
- **市场前景 30%** — 高校研究生、社科研究者、NGO 调研人员都是高频刚需用户，具备持续演进空间

## 降级策略

| 缺失组件 | 降级行为 |
|---------|---------|
| DashScope SDK | 规则回复（基于关键词匹配） |
| FlyAI CLI | 跳过旅行搜索，纯文本方案 |
| FlyAI 体验模式（无正式 Key） | 正常搜索但价格脱敏（如 ¥5x）、结果受限，systemMessage 提示透传给用户 |
| 两者都缺失 | 基础问答模式 |

## License

MIT
