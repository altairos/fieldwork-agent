# 开发指南 (Development Guide)

面向贡献者/后续开发者的工程手册：环境搭建、运行、测试、CI、架构与扩展点。
使用者视角的快速上手请看 [README.md](README.md)。

---

## 1. 环境搭建

本项目用 `pyproject.toml` 作为唯一打包入口。仓库根目录即 Python 包
`fieldwork_agent`（通过 `[tool.setuptools] package-dir` 映射），**目录名与包名不同是正常的**——
不要重命名仓库目录，靠可编辑安装即可。

```powershell
# 需要 Python >= 3.10
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 运行时 + 开发依赖（pytest / ruff）
pip install -e ".[dev]"
```

安装后可用两种方式启动：
- `python -m fieldwork_agent`
- `fieldwork`（console script，见 `[project.scripts]`）

### 外部依赖

| 能力 | 安装 | 缺失时行为 |
|------|------|-----------|
| LLM 对话 | `pip install dashscope`（已含在依赖里） | 降级为规则回复 |
| 旅行搜索 | `npm i -g @fly-ai/flyai-cli` | 跳过搜索，纯文本方案 |
| 地理底座 | 无需安装（HTTP API），配置 `AMAP_API_KEY` | 高德原语统一返回 `amap_invalid_key`，画像/接驳降级 |

FlyAI 密钥（`~/.flyai/config.json` 的 `key` 字段）由 `Config` 自动读取，见第 3 节。

---

## 2. 测试 Harness

测试**全部基于 mock**，不需要 API Key、不需要 flyai CLI、不联网，因此可在 CI 直接跑。

```powershell
pytest            # 运行全部（配置见 pyproject 的 [tool.pytest.ini_options]）
pytest -k flyai   # 按关键字筛选
ruff check .      # 静态检查（规则见 [tool.ruff.lint]）
```

### 测试结构

```
tests/
├── conftest.py               # FakeConfig + fake_llm / fake_flyai 宿主测试桩
├── repository_contract.py    # L2 repository 契约套件（REPO_FACTORIES 参数化，Plan 5 双跑）
├── test_config.py            # 三级加载优先级：env → config.json → ~/.flyai
├── test_json_repo.py         # JsonRepository：契约实现 + 落盘布局 + 存量兼容 + 原子写
├── test_migrate.py           # JSON→SQLite 存量搬迁：7 类全等 + 幂等 + schema_meta 标记
├── test_guidance.py          # 四阶段常量/提示词、check_advance 推进判定、extract 纯函数
├── test_models_registry.py   # 包络/模型与 registry 能力元数据契约
├── test_registry_surface.py  # 生产态能力面：裸导入即注册 23 项 + registry handler 统一包络
├── test_sources_flyai.py     # 8 搜索原语 argv 拼装 + 异常折进包络
├── test_sources_amap.py      # AMapClient 6 原语参数拼装 + 包络折错 + 无 Key 短路不出网
├── test_itinerary.py         # compose_itinerary 聚合（legs/stays/budget coverage）
├── test_last_mile.py         # plan_last_mile 聚合（双 geocode → 跨城 transit，端失败即止/降级语义）
├── test_site_profile.py      # profile_field_site 聚合分支（含 amap geo/facilities 段）
├── test_documents.py         # compose_proposal 八章 / 访谈指南 / suggest_informants
├── test_llm_config.py        # LLMClient 与模型分档、能力降级
└── test_loop_orchestrator.py # run_tool_loop 回合循环 + 宿主分派/降级/落盘
```

约定：
- **隔离**：涉及文件系统的测试用 `tmp_path`；`conftest.FakeConfig` 把项目目录指向临时路径，
  绝不读写真实 `~/.fieldwork_agent`。`test_config.py` 通过 monkeypatch
  `CONFIG_DIR` / `PROJECTS_DIR` / `pathlib.Path.home` 隔离用户主目录。
- **不碰网络/子进程**：`kernel/sources/flyai.py` 测试 monkeypatch `_run_command`（测拼装）
  或让其抛异常（测包络折错）；`dashscope` 从不在测试里真实调用。
- **新增用例原则**：优先针对纯逻辑函数（`check_advance`、`extract_locations`、
  `compose_itinerary` + `StubClient`）写断言，行为可预测、无外部依赖。

---

## 3. 密钥与配置

`Config`（[`kernel/config.py`](kernel/config.py)，A09 自 core 下沉）的加载优先级：

- **DashScope / 模型**：环境变量初始化 → 若存在 `~/.fieldwork_agent/config.json` 则**覆盖** env。
  密钥键：`DASHSCOPE_API_KEY`。模型三槽（对话推进 / 长文方案 / 结构化抽取）：
  env 为 `FA_INTERVIEW_MODEL`、`FA_PROPOSAL_MODEL`、`FA_EXTRACT_MODEL`
  （默认 `qwen3.8-flash` / `qwen-max` / `qwen3.8-flash`），config.json 对应
  `interview_model` / `proposal_model` / `extract_model` 覆盖 env。
- **FlyAI**：环境变量 `FLYAI_API_KEY` 优先；缺失时回退读取 `~/.flyai/config.json` 的 `key`。
- **高德**：环境变量 `AMAP_API_KEY` 初始化 → `~/.fieldwork_agent/config.json` 的
  `amap_api_key` 同样覆盖 env。`AMapClient(api_key="")` 无 Key 时所有原语在 `_call`
  统一短路返回 `amap_invalid_key` 包络（不出网），宿主无需额外判断。
- **高德 JS Key（Web 地图面板）**：环境变量 `AMAP_JS_KEY` + `AMAP_JS_SECURITY`
  （JS API 2.0 安全密钥）→ `~/.fieldwork_agent/config.json` 的
  `amap_js_key`/`amap_js_security` 同样覆盖 env（文件在用户主目录，Windows：
  `C:\Users\<用户名>\.fieldwork_agent\config.json`，不在项目目录内）。JS Key 与服务 Key **分离申请**（前端明文暴露，靠安全密钥
  域名白名单防护，参考 [jscode 指南](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)）；
  缺失时 `/settings/status` 返回 `amap_js_configured: false`，前端 `AmapMapPanel`
  降级为坐标文字卡（坐标与设施清单仍可读）。
- **公网绑定声明（S01）**：`FIELDWORK_PUBLIC_BIND=1` 表示本实例对外监听，
  启动期强制要求 `FIELDWORK_APP_TOKEN` 非空，否则 `create_app` 抛 `RuntimeError`
  拒绝启动（避免“令牌留空 = 全站开放”配上公网直连）；不设该变量时行为不变。
  部署物已在 `deploy/aliyun/fieldwork-web.service` 声明，本地开发无需关心。
- **限流与日配额（S02）**：`web_host/ratelimit.py` 进程内滑动窗口/日配额，
  `init_deps` 挂到 `app.state.rate_limits`。登录按 IP 与账号双维度（默认
  10 次/分钟）、注册按 IP（默认 20 次/小时）、回合按 owner（默认 200 次/天，
  服务器本地日期）；超限 429 带 `Retry-After`。对应 `FA_LOGIN_RATE_LIMIT` /
  `FA_LOGIN_ACCOUNT_RATE_LIMIT` / `FA_REGISTER_RATE_LIMIT` / `FA_TURN_DAILY_QUOTA`。
  状态在进程内：多 worker / 多实例部署需换共享后端。
- **能力开关**：`dashscope_available` / `flyai_available` / `amap_available` / `dingtalk_available` 供上层降级判断。

设置 flyai 正式 Key（CLI 无 `login` 命令，体验模式无需 Key）：

```powershell
# 从 https://flyai.open.fliggy.com/ 获取正式 Key 后：
flyai config set key <YOUR_API_KEY>     # 写入 ~/.flyai/config.json 的 "key" 字段
```

> 密钥属敏感信息：`.gitignore` 已忽略 `~/.flyai`、`.env`、`config.json`。切勿提交。

---

## 4. 架构与数据流

能力内核化后的分层（旧单体决策核心 dialogue_engine/state 与 tools/ 已删除，内核为唯一实现）：

```
CLI (core/cli.py)
  └─ FieldAgent (core/orchestrator.py)      内核宿主：只做组装与编排，无业务规则
       ├─ runtime/loop.py                   run_tool_loop — CLI/Web 共用回合循环（LLM↔工具多轮）
       ├─ kernel/registry.py                能力注册表 + build_tool_schemas（工具 schema 单一来源）
       ├─ kernel/sources/flyai.py           FlyAIClient — 8 搜索原语 → 归一化 Envelope
       ├─ kernel/sources/amap.py            AMapClient — 高德 6 原语（geocode/regeocode/around/
       │                                    text/transit/weather）→ 归一化 Envelope，无 Key 短路
       ├─ kernel/aggregation/               本地聚合：profile_field_site / compose_itinerary /
       │                                    plan_last_mile（末端接驳）/ analyze_fieldwork_window
       ├─ kernel/documents/                 compose_proposal / compose_interview_guide / suggest_informants
       ├─ kernel/guidance/                  五阶段常量·提示词·check_advance + extract 纯函数
       ├─ kernel/repository/                ProjectRepository 协议 + JsonRepository 持久化
       ├─ kernel/models.py                  共享 pydantic 契约：Envelope/ToolError/SearchItem/
       │                                    ResearchContext/ProposalDoc/BudgetBreakdown…（全内核复用）
       ├─ kernel/llm.py                     LLMClient（DashScope OpenAI 兼容端点，function calling）
       └─ kernel/config.py                  Config（env/config.json 优先级、目录布局、单进程断言）
```

测试夹具（fixtures）：全部集中在 `tests/conftest.py` —— `FakeConfig`（tmp 目录底座）、
`fake_llm`（ScriptedLLM 形状，按脚本返回 chat 响应并记录 messages）、`fake_flyai`（StubClient
形状，8 原语恒返单条目成功包络并记录调用）；宿主/聚合测试一律注入这两个桩，不打真实网络。

依赖方向单向：`cli → orchestrator → {runtime, kernel}`；`runtime` 只 import `kernel`；
**`kernel` 绝不反向 import `core/`、`runtime` 或任何宿主**（只依赖标准库 + pydantic，无状态、
不编排 LLM）。工具决策由宿主 LLM function calling 完成——宿主把 registry 生成的 schema 交给
模型，将其 `tool_calls` 经 `client_kernel_dispatch` 分派回内核能力。
`Config` 为无状态底座（`kernel/config.py`，A09 自 core 下沉），由 `core`、`web_host`、
`mcp_server` 共同读取；`core/config.py` 只是旧 import 路径的 re-export 兼容层（不转发
monkeypatch，测试请 patch `kernel.config`）。

**部署硬约束：单进程（A04）**。`web_host` 的 `TurnManager` 把回合状态、SSE 订阅队列
与写锁都放在进程内存，`events.jsonl` 也是无跨进程锁的追加写——多 worker/多副本会
立即产生事件丢失、`seq` 冲突与写锁失效。`create_app` 启动期检查
`FA_WEB_WORKERS` / `UVICORN_WORKERS` / `WEB_CONCURRENCY`，大于 1 直接拒绝启动；
部署命令禁止加 `--workers`。扩容需先把这些状态迁到共享后端（Redis/DB）再另行设计。

**四个工作面 + finished 终态**（`kernel/guidance/phases.PHASES`）：
`research_positioning → field_site_profiles → itinerary_planning → proposal_generation → finished`
（A–D 是左栏展示的四个工作面；`finished` 是方案确认后的终态，不是第五个工作面）
推进判定是**纯函数 `check_advance`**，由宿主在每轮末尾确定性执行（绝不由 LLM 宣布）：
A→B 看必填定位字段；B→C 需 confirmed 田野点 + ≥1 访谈对象（非访谈型可 `interviews_not_applicable`
显式跳过）；C→D 需行程；D→finished 需草稿存在**且**用户确认 `proposal_confirmed`。

**持久化布局**（每个项目在 `~/.fieldwork_agent/projects/<id>/`，由 `JsonRepository` 管理并
对旧版 state 做读时归一）：
`state.json` · `chat_history.jsonl`（全量单轨，state 内只留 50 条窗口）· `knowledge/*.json` ·
`search_cache/*.json` · `output/proposal_*.md`。

---

## 5. 扩展点

### 5.1 新增/修改一个工具能力
1. 在 `kernel/sources/flyai.py`（原子搜索）或 `kernel/aggregation|documents/`（聚合/文档）
   加方法/函数，按既有模式 `@register` 进 registry（名称、描述、`args_model` 契约元数据）；
   flyai 命令名/参数名须与 `flyai <command> --help` 完全一致。
2. `registry.build_tool_schemas()` 自动生成宿主/MCP 共用 schema，**无需手写 schema**；
   在 `tests/test_sources_flyai.py`（argv 拼装）或对应聚合测试文件补契约断言。
3. 宿主侧在 `core/orchestrator.py` 的 `_CAPABILITY_DISPATCH` 绑定实现与注入方式
   （`client` = 注入 FlyAIClient，`amap_client` = 注入 AMapClient，`llm` = 注入 LLMClient）。
   MCP/Web 宿主同源：MCP 分派表内 amap 六原语与 `plan_last_mile` 声明 `amap_client` 注入接缝。

### 5.2 工具决策与意图路由
已无关键词路由——`DialogueEngine.decide_tool_calls` 随旧核心删除，**工具决策移交宿主 LLM
function calling**：模型读 registry schema 自行决定调用哪个能力，宿主只做校验、分派与落盘。
调整"何时用什么工具"的引导，改 `core/orchestrator.py` 的 `_TOOL_GUIDANCE` 与各阶段提示词。

### 5.3 新增访谈阶段
1. 在 `kernel/guidance/phases.py` 的 `PHASES` 插入阶段标识，并补 `REQUIRED_FIELDS`、
   `SYSTEM_PROMPTS`、`GUIDING_QUESTIONS` 对应条目；
2. 在 `check_advance` 里补该阶段的进阶判定（保持纯函数、确定性）；
3. 更新 `core/cli.py` 的 `_PHASE_LABELS` / `_PHASE_CN` 展示映射（键用 kernel 常量）。

### 5.4 新增命令
在 `core/cli.py` 主循环的 `if cmd == ...` 分发中追加分支，并在 `print_help()` 登记。

### 5.5 Web 宿主扩展点（web_host/）

- **新增 SSE 事件类型**：修改 `web_host/events.py`（追加 pydantic 模型）+ `sse.py` 编码
- **新增 REST 端点**：在 `web_host/api/` 对应 router 文件追加
- **Agent loop 扩展**：`web_host/agent/loop.py` 的 `run_turn`
- **回合协议（R4）**：`web_host/turn_manager.py`
- **新增工具卡片**（前端）：见 §5.6（registry 加一行映射 + 写卡片组件）

### 5.6 前端扩展点（web/）

- **新增工具卡片**：内核新增能力后，前端只需 ① 在 `web/src/components/cards/registry.tsx` 的
  `toolCardRegistry` 加一行 `工具名: 卡片组件` 映射 ② 写一个卡片组件（props: `{data, degraded}`，
  消费 `tool_completed.data`，价格走 `Price` 组件）。未注册工具自动落 `ToolCardFallback`
  （JSON 折叠），不报错。
- **视觉事实源**：`docs/frontend/design-board.html`（学术档案·纸感温润）。改视觉先改设计板，
  token 值与 `web/tailwind.config.ts` 严格同源。界面禁 emoji，图标用线形 SVG
  （`web/src/components/ui/icons.tsx`）。
- **SSE 事件**：六事件协议见 `web/src/types/events.ts`（对齐 `web_host/events.py`）。
  新增事件类型需同步 events.ts + 四 store 的 applyEvent。
- **状态层**：四 zustand store（project/conversation/tools/ui），每个 `applyEvent(SseEvent)`
  只处理相关事件。右栏不自动切 Tab（phase_transition 只置 pendingSuggestion）。
- **构建挂载**：`npm run build` 产物 `web/dist` 由 `web_host/app.py` 的 `_mount_frontend`
  自动挂载（API 路由优先 + SPA 深链 fallback + dist 缺失时静默跳过）；`web/dist` 已被
  `.gitignore` 排除，不入库。

### 5.7 新增 Repository 实现

`kernel/repository/protocol.py` 是唯一契约；新实现（如 PostgreSQL、Serverless DB）只须过
`tests/repository_contract.py` 的**双跑门禁**——在 `REPO_FACTORIES` 追加一个工厂，
全部契约用例即在新后端自动重跑（Json/Sqlite 已是既有双跑基线）。存量数据搬迁参照
`core/migrate.py` 的 raw-import 模式（保留原 id/时间戳 + 行数/抽样哈希校验 + 项目级幂等标记）。

---

## 6. CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml)：`ubuntu` + `windows` 双平台、
Python 3.11/3.12 矩阵，执行 `pip install -e ".[dev]"` → `ruff check .` → `pytest -ra`。
Windows job 专门回归 emoji/UTF-8 输出路径（见第 7 节）。

---

## 7. 平台注意事项 / 已知坑

- **Windows GBK 终端 + emoji**：中文 Windows 控制台默认代码页 936，`print` emoji 会触发
  `UnicodeEncodeError`。`core/cli.py` 的 `_ensure_utf8_stdio()` 在入口把 stdout/stderr
  切到 UTF-8。**新增任何带 emoji 的输出都依赖此函数，别在它之前 print。**
- **prompt_toolkit 需真实 TTY**：用管道喂 stdin（`"x" | python -m fieldwork_agent`）会在
  `create_output()` 处失败——这是交互库对无终端的限制，非 bug。验证启动逻辑请用真实终端，
  或直接对 `kernel`/`core` 单元逻辑写 pytest。
- **`flyai.ps1` 执行策略**：PowerShell 若拒绝加载 `.ps1`，改用 `flyai.cmd`，
  或 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。
- **`~` 不自动展开**：给 prompt_toolkit / 文件路径用绝对路径（`CONFIG_DIR / ...`），
  不要传字面 `"~/..."`。

---

## 8. 代码风格

- `ruff` 规则集 `E,F,I,UP,B`，行宽 100（`E501` 已豁免）；`import` 顺序由 `I` 保证。
- 提交前本地跑 `ruff check . && pytest`。
- 注释与文档用中文，术语/代码标识符保留原文。

---

## 9. Roadmap（后续可选项）

- [x] 打包与依赖清单（pyproject / requirements）
- [x] 单元测试 harness（kernel 各子包 + repository 契约 + 宿主 loop）+ CI
- [x] Windows UTF-8 输出修复
- [x] 能力内核化：kernel 纯能力层 + 宿主 function calling（本计划，旧核心已删除）
- [x] 聚合策略分支测试（`kernel/aggregation`：`profile_field_site` / `compose_itinerary`）
- [x] `proposal_generation` 结构化输出模板（八章 compose_proposal + mode 回退声明）
- [x] MCP Server / Web 宿主（FastAPI + SSE 实时事件流 + 回合管理）
- [x] 高德地理底座 PoC：`kernel/sources/amap.py` 六原语 + 画像 geo/facilities 段 + `plan_last_mile` 末端接驳聚合（设计见 `docs/plans/2026-10-18-amap-poc-design.md`，真 Key 冒烟 7/7 通过，存档 `tools/spike_amap.py`）
- [x] 高德 PoC2：`plan_last_mile` 班次日期透传（`date_hint` 已实现并有测试；其他扩展另行规划）
- [x] 流式输出测试：`kernel/llm` 接口与 Web Agent loop 已有 mock 测试覆盖
- [x] 集成测试：以 fake FlyAI stub 跑通四阶段主流程（`tests/test_web_mainline_e2e.py`）
- [ ] 钉钉同步（`sync dingtalk`）的测试覆盖

### 9.1 当前产品与工程待办

- [ ] CLI REPL 接入 `FA_STORAGE=sqlite`，与 Web/MCP 共用仓储工厂；来源：[README 存储后端说明](README.md#存储后端与迁移)。
- [ ] 建立钉钉同步 `sync dingtalk` 的成功、未配置、无方案及网络失败测试；来源：本节测试盲区。
- [ ] 完成“中栏证据双 Tab 与检索修复”计划：搜索答案传递、证据与缓存关联、证据面板及回合恢复治理；该计划对应改动当前在工作区，尚待合并和验收。详见 [`2026-10-19-middle-column-evidence-tabs-and-search-fixes.md`](docs/superpowers/plans/2026-10-19-middle-column-evidence-tabs-and-search-fixes.md)。
- [ ] 核验并完成 Tier S 生产上线：先确认多组快捷回复代码已部署，再启用私有资产并验证无标记泄漏；详见 [`2026-10-19-multigroup-tierS-rollout-plan.md`](docs/plans/2026-10-19-multigroup-tierS-rollout-plan.md)。上线状态应在执行前重新核对。

### 9.2 后续能力规划（尚未排期）

- [ ] **可追溯学术知识检索（RAG，M0–M5）**：先冻结研究背景字段与评测基线，再实现版本化资料、审核知识卡、中文混合检索、主动证据准备、方案引用依赖、CLI/Web/MCP 与来源界面，最后完成固定评测、部署和恢复验收。详细任务与门槛见 [`2026-10-17-rag-development-plan.md`](docs/plans/2026-10-17-rag-development-plan.md) 和 [`2026-10-19-rag-knowledge-corpus-plan.md`](docs/plans/2026-10-19-rag-knowledge-corpus-plan.md)。
- [ ] **海外地理与交通供应商**：确定首批地区和真实样例，定义通用 provider 契约，接入一个海外 REST 服务，再完成末端接驳、缓存与来源、配置诊断、三宿主接线和在线覆盖/费用验证。详细任务 G01–G12 见 [`2026-10-18-overseas-geodata-provider-plan.md`](docs/plans/2026-10-18-overseas-geodata-provider-plan.md)。高德目前仍是境内底座；境外能力尚无替代供应商链路。
- [ ] **Web 账号与身份一期**：实现用户/会话存储（JSON 与 SQLite）、注册登录退出、会话失效处理、匿名项目认领、owner 运行时透传及前端登录流程，并完成双仓储契约和浏览器隔离验收。详细步骤见 [`2026-10-19-user-account-identity-phase1.md`](docs/superpowers/plans/2026-10-19-user-account-identity-phase1.md)。
- [ ] **开题与基金方案体例**：在现有英美式八章体例之外，按既定决策分别深化高校开题版和国社科基金版的章节、研究进度/预算视图、伦理提示与文献格式；实施前需按当前有效规范复核。调研和原始行动项见 [`DEEP_RESEARCH_社科research-proposal体例核验.md`](docs/research/DEEP_RESEARCH_社科research-proposal体例核验.md)；该方向此前被决定暂缓深挖。
- [ ] **文献检索质量评测**：基于真实研究主题建立人工相关性判断集，评估 OpenAlex 多路检索词、候选排序、中文来源补检与证据边界；详见 [`openalex-search-design.md`](docs/research/openalex-search-design.md)。近期已有检索能力改进提交，评测集与人工质量基线仍需单独确认。

### 9.3 规划范围说明

- `docs/reviews/2026-10-19-项目开发现状全面复审.md` 中的 N01–N15 修复已由 `d946b18` 合入；复审中的历史问题不再作为当前待办重复列出。后续若复审发现新问题，按新证据更新本路线图。
- 研究写作 Agent v1 文档明确标注为历史方向草案，当前不作为实施计划；只有重新确认产品方向并编写新规格后再纳入路线图。
- 各计划文档记录的是设计或阶段任务，不代表其全部内容都已排期；实施前应先核对当前代码、分支和实际部署状态。

## 10. 资产分级与 secrets 维护

本项目的 prompt 体系与访谈示范档案库分三级管理，开源主仓只含公开层：

| 级别 | 内容 | 存放位置 | 加载方式 |
|---|---|---|---|
| **Tier S（私有）** | 完整版示范库（11 类词条）、完整访谈方法论、四阶段完整 system prompt、生成类完整提示词 | `secrets/`（独立私有 git 仓库，被主仓 `.gitignore` 忽略） | 运行时覆盖 Tier P |
| **Tier P（公开）** | 精简版：3 词条示范库、简版方法论、四阶段简版 prompt、简版 generation 措辞 | `kernel/assets.py` 内置常量 | 默认 |
| **Tier O（常开）** | 算法与契约常量（`GUIDING_QUESTIONS`、`REQUIRED_FIELDS`、`check_advance`、`match_examples`、`PROPOSAL_SECTIONS`、各 user prompt 的输出契约段） | 各模块代码内 | 直接 import，不经 assets |

**探测链**（`kernel/assets.py`，先命中先用）：

1. 环境变量 `FIELDWORK_ASSETS_DIR` 指向的目录
2. `<repo>/secrets/`
3. `~/.fieldwork_agent/assets/`
4. 内置公开版

覆盖规则：按资产文件**整体替换**（不做深层合并），覆盖包未提供的资产回落公开版。坏 JSON、结构校验失败、env 指向不存在的目录，一律抛 `AssetsError`（消息含出错文件路径）。测试由 `tests/conftest.py` 的 autouse fixture 钉在公开版——本地放置 `secrets/` 不影响测试结果。

**维护约定：**

- 改完整版资产：直接编辑 `secrets/` 下对应文件（`informant_library.json`、`example_types.json`、`interview_methodology.txt`、`system_prompts.json`、`generation_prompts.json`），无需动主仓代码；改完在 secrets 仓库提交（`git -C secrets add -A` + `git -C secrets commit`），并跑行为评估集冒烟防止调优引入回退——评估集尚未建立前以手动冒烟代之（用完整版生成一份访谈提纲，人工对比改前输出）。`secrets/export_from_legacy.py` 是一次性迁移脚本（依赖迁移时刻的旧常量），迁移完成后仅作历史留存，勿再运行。
- 公开版**只降不升**：`kernel/assets.py` 内的公开版只随开源需要精简，不从 secrets 回抄内容；两版标题与标志性措辞必须保持可区分（历史重写时靠特征串识别，见 `secrets/feature-strings.txt`）。
- 新增词条或提示词后：直接编辑 `secrets/` 对应文件，并把新内容中的标志性私有短语逐条追加到 `secrets/feature-strings.txt`（该清单供 M3/M4 历史重写的 blob 识别用）；随后确认 `python tools/check_secrets_leak.py` 仍输出 clean。
- secrets 私有仓库的远程必须托管在**私有**仓库（自建 Gitea 或私有 GitHub repo），推送前确认可见性为 private；主仓永远不提交 `secrets/` 下任何文件。
- 新增能力若引入新 prompt 资产：在 `kernel/assets.py` 同时定义公开版默认值与对应资产文件键（Tier S 覆盖路径自动生效），并把私有版独有短语追加进 `secrets/feature-strings.txt`。
