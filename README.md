# 田野罗盘 (Fieldwork Agent)

> 面向人类学/社会学田野调查者的 AI 旅行规划助手
> 基于飞猪 FlyAI 全品类旅行搜索、高德地理底座与 OpenAlex 学术文献检索，引导式访谈 → 辅助生成研究方案（Research Proposal）草稿

## 项目用途

**田野罗盘**帮田野调查者完成从"想去哪里"到"怎么抵达、住在哪里、调研什么"的全流程筹备：

- **引导式访谈**：五阶段推进（研究定位 → 田野点画像 → 行程规划 → 方案生成 → 已完成），推进判定为确定性纯函数，不由 LLM 宣布。
- **真实数据检索**：深度集成飞猪 FlyAI 全部 8 个搜索工具（航班/火车/酒店/POI/关键词/AI 语义/万豪酒店/万豪套餐），行程、预算、文化场所均基于实时搜索数据并标注来源。
- **地理空间底座**：接入高德开放平台 Web 服务 API（地理编码/逆编码/周边检索/关键词搜索/跨城公交/天气共 6 个原语）——田野点画像的地理定位与周边设施段、"枢纽 → 田野点"末端接驳规划（`plan_last_mile`）基于真实地理数据，覆盖境内。
- **学术文献检索**：内置 `search_literature`（OpenAlex 主源 + ncpssd/PubScholar 中文深链）——将研究问题拆为对象、现象、情境、理论/方法四类要素，生成 2–4 路检索矩阵（同义词 OR、概念组 AND），先 probe 后按偏差收窄或放宽；结果绑定当前研究范围，候选文献不自动采纳为事实。
- **本地聚合与估算**：田野点画像（含周边设施）、多段行程组装、末端接驳、调研时窗分析（多日期价格采样 × 节庆覆盖 × 预算可行性）、驻村生活成本区间估算（`estimate_living_cost`，按聚落类型 × 就餐方式查表，不返回单点精确值）——均为本地组合计算，不依赖额外服务。
- **方案产出**：生成结构完整的研究方案 Markdown 草稿（当前为英美式八章体例，适用海外/港澳申请与课程场景；高校开题、国家社科活页体例在规划中）。
- **三种宿主形态**：交互式 CLI、FastAPI Web 宿主（REST + SSE 实时事件流 + React 前端）、标准 MCP Server。

架构分层：`core/`（宿主编排）→ `runtime/`（共用回合循环）→ `kernel/`（无状态能力内核，只依赖标准库 + pydantic，绝不反向 import 宿主层）。开发者细节见 [DEVELOPMENT.md](DEVELOPMENT.md)。

**内容安全**：三层防护——system prompt 安全边界段（含研究议题豁免：边缘群体、宗教、越轨行为、物质使用等正当社科议题不受限）；输入侧组合匹配硬拦截（意图动词 × 危害对象同现才命中，命中返回固定拒答、不消耗模型调用）；输出侧落库前终检（窄词表防拒答自触发，拦截内容不污染对话历史）。平台审核错误（`data_inspection_failed`）与 Key 错误分流提示。判定内核见 `kernel/guidance/safety.py`。

## 安装步骤

### 后端（Python >= 3.10）

```bash
# 仓库根目录执行
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

# 运行时 + 开发依赖（pytest / ruff / httpx）
pip install -e ".[dev]"
# 仅运行时依赖
pip install -e .

# MCP 宿主需要可选依赖
pip install -e ".[mcp]"
```

> 仓库根目录即 Python 包 `fieldwork_agent`（通过 `[tool.setuptools] package-dir` 映射），目录名与包名不同是正常的，不要重命名仓库目录。

### 前端（Node >= 22.22.2）

```bash
cd web
npm install
```

### 外部依赖（可选）

| 能力 | 安装命令 | 缺失时行为 |
|------|---------|-----------|
| LLM 对话 | 随 pip 依赖安装（dashscope），需配置密钥 | 降级为规则回复 |
| 旅行搜索 | `npm i -g @fly-ai/flyai-cli` | 跳过旅行搜索，纯文本方案 |
| 地理底座 | 无需安装（HTTP API），配置 `AMAP_API_KEY` | 高德原语返回 `amap_invalid_key`，画像/接驳自动降级，其余功能不受影响 |

## 运行方式

### CLI

```bash
fieldwork                # console script（需 pip install -e .）
python -m fieldwork_agent   # 等价
```

### Web 宿主（FastAPI + 内置前端）

```bash
fieldwork-web            # 监听 http://127.0.0.1:8300
```

`web/dist` 存在时自动挂载到 `/`（API 路由优先，SPA 深链 fallback）；dist 缺失时仅提供 API。

### Web 前端（开发期）

```bash
cd web
npm run dev              # Vite 开发服务器 :5173，/projects /health /settings 代理 → :8300
npm run mock             # 无后端联调：mock SSE 服务器 :8301
npm run build            # 构建产物 → web/dist
npm run test             # vitest 单元/组件测试
npm run test:e2e         # Playwright 端到端（首次需 npx playwright install）
```

## 存储后端与迁移

项目数据默认落 JSON 文件树（`~/.fieldwork_agent/projects/`，`JsonRepository`）；
也可切换单文件 SQLite 后端（`SqliteRepository`，WAL 模式，CLI/Web/MCP 同一契约）：

```powershell
$env:FA_STORAGE = "sqlite"   # 切换后端（默认 json；未知值安全回落 json）
```

> 注意：CLI REPL（`fieldwork` 命令行交互）当前仍直接使用 JSON 存储，`FA_STORAGE=sqlite` 暂只对 Web 宿主与 MCP Server 生效——CLI 侧接入属后续任务。

已有 JSON 存量项目可一次性搬入 SQLite（幂等，可重复执行）：

```powershell
fieldwork migrate --to-sqlite                              # 全部项目
fieldwork migrate --to-sqlite --project-id my_research     # 单个项目
fieldwork migrate --to-sqlite --force   # 同名存量项目：备份后覆盖重迁（R03）
```

- **迁移范围**：8 类数据——项目/阶段（含 `phase_history`）、研究上下文、全量消息史
  （`chat_history.jsonl`）、田野点、报道人、搜索缓存、知识库、提案文档（含旧
  `proposal_draft`/`final_proposal` 单值键）、确认记录（`confirmations` +
  `proposal_confirmed`）。迁移后自动做行数与抽样字段校验，不一致即报错终止，
  不留半迁移状态。
- **迁移期单写者**：迁移期间请停止对同一批项目的其他写入（只跑迁移进程），完成后再
  设 `FA_STORAGE=sqlite` 启动宿主，避免双后端并发写造成分叉。
- **冲突保护**：SQLite 侧已存在同名项目且无迁移标记时，`migrate` 默认报错中止（不
  清空存量）；`--force` 先用 sqlite3 Backup API 做滚动备份（`fieldwork.db.bak.1`
  最新，共留 3 份）再整项目清空重导。
- **JSON 只读回退**：迁移不删除、不改动 JSON 目录——不设 `FA_STORAGE`（或设回
  `json`）即回到 JSON 后端；SQLite 库（`~/.fieldwork_agent/fieldwork.db`）留存，
  可随时删除重迁，天然回滚。

### MCP Server

```bash
fieldwork-mcp                                      # console script
python -m fieldwork_agent.mcp_server               # 等价（stdio）
.venv/Scripts/python.exe demo_mcp_client.py        # 演示客户端（手动运行，不进 CI）
```

MCP 能力面：32 个工具（23 个内核能力 + 9 个状态工具）+ 资源面 + 2 个 prompts。

部署形态下 Web 进程同时暴露 Streamable HTTP 端点 `/mcp`（与页面同端口），可直接注册到
阿里百炼等 MCP 客户端；多租户 key 归属（`x-flyai-key` / `x-dashscope-key` 动态 Header
逐请求覆盖服务器 env 兜底）见 [deploy/aliyun/DEPLOY.md](deploy/aliyun/DEPLOY.md) 第 7 节。

### 测试与静态检查

```bash
pytest              # 全部基于 mock：不需要 API Key、不联网、不起子进程
pytest -k flyai     # 按关键字筛选
ruff check .
```

### 服务器部署（阿里云轻量服务器）

完整流程见 [deploy/aliyun/DEPLOY.md](deploy/aliyun/DEPLOY.md)（一键脚本 `deploy/aliyun/deploy.sh`、systemd 单元、Nginx 反代配置均在 `deploy/aliyun/`）。

- 监听端口 **2143**，访问地址：（待补：公网 IP 或已备案域名）
- scp 上传地址、服务器登录方式：（待补）
- 部署后临时访问：`http://<待补：公网IP>:2143/`

## 使用示例

### CLI 五阶段交互

```
fieldwork > new my_research
✅ 新项目已创建: my_research

fieldwork [my_research] (📍 研究定位) >
我是社会学博士生，想研究西南少数民族村落的宗族组织变迁。
单人调研，计划3个月驻村观察，经费来自国家社科基金青年项目，预算2万元，
时间窗口是明年6-9月。

💡 引导问题:
  • 您对田野地点的确定程度如何？（已明确地点 / 有区域范围 / 希望我推荐）
```

阶段随信息完备自动推进（提示符中的 `(📍 研究定位)` 等标签同步变化）；`generate` 生成方案草稿。

### CLI 命令一览

| 命令 | 说明 |
|------|------|
| `new [name]` | 创建新项目 |
| `list` | 列出所有项目 |
| `open <id>` | 打开已有项目 |
| `chat <消息>` | 发送对话消息（不带参数进入逐行输入模式；直接输入文本等同） |
| `generate` | 基于当前所有信息生成研究方案草稿（Markdown，投稿/开题前请人工核校） |
| `confirm site <id>` / `confirm proposal` | 确认田野点 / 确认方案草稿（触发确定性阶段推进；不带参数查看待确认清单） |
| `knowledge add <标题>` | 添加自有研究资料到本地知识库 |
| `knowledge list` | 查看知识库条目 |
| `status` | 查看当前项目完整状态 |
| `sync dingtalk` | 将最新方案同步到钉钉群（需配置 webhook） |
| `help` | 显示帮助 |
| `quit` / `exit` / `q` | 退出 |

### Web API（节选）

```bash
# 创建项目
curl -X POST http://127.0.0.1:8300/projects

# 创建回合（返回 turn_id）
curl -X POST http://127.0.0.1:8300/projects/<id>/messages -H "Content-Type: application/json" -d '{"content": "..."}'

# SSE 事件流
curl http://127.0.0.1:8300/projects/<id>/turns/<turn_id>/events
```

主要端点：`POST /projects`（创建）、`GET /projects`、`GET /projects/{id}`、`DELETE /projects/{id}`、`GET/POST /projects/{id}/messages`（消息历史/创建回合）、`GET /projects/{id}/turns/{tid}/events`（SSE）、`POST /projects/{id}/turns/{tid}/cancel` 与 `.../retry`、`GET /projects/{id}/cached-searches`（证据与搜索缓存关联）、`GET /projects/{id}/proposal`（及 `/download`、`/proposals` 历史版本与 `/proposals/{doc_id}/download`）、`PUT /projects/{id}/research-context`、`POST /projects/{id}/field-sites/{sid}/confirm`、`POST /projects/{id}/informants`、`POST /projects/{id}/proposal/confirm`、`GET /settings/status`、`GET /health`、`GET /ready`。

## 关键配置说明

### 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `DASHSCOPE_API_KEY` | 是（LLM 功能） | 阿里云百炼/通义千问密钥，缺失时降级为规则回复 |
| `FLYAI_API_KEY` | 否 | 飞猪 FlyAI 密钥；未设置时自动回退读取 `~/.flyai/config.json` 的 `key` 字段 |
| `AMAP_API_KEY` | 否 | 高德开放平台 Web 服务 Key（[console.amap.com](https://console.amap.com) 申请）；也可写入 `~/.fieldwork_agent/config.json` 的 `amap_api_key`。缺失时高德原语统一返回 `amap_invalid_key`，其余功能不受影响 |
| `AMAP_JS_KEY` / `AMAP_JS_SECURITY` | 否 | 高德 Web 端（JS API 2.0）Key 与安全密钥，仅供 Web 地图面板；须与 `AMAP_API_KEY`（Web 服务 Key）**分离申请**（控制台选“Web 端（JS API）”类型），安全密钥配域名白名单。也可写入 `~/.fieldwork_agent/config.json`（Windows：`C:\Users\<用户名>\.fieldwork_agent\config.json`）的 `amap_js_key`/`amap_js_security`。缺失时地图面板降级为坐标文字卡，其余功能不受影响 |
| `FIELDWORK_APP_TOKEN` | 否 | Web 宿主访问令牌；非空时所有 `/projects`、`/settings` 接口要求 `X-App-Token` 请求头匹配（仅环境变量，不落盘） |
| `FA_INTERVIEW_MODEL` | 否 | 对话推进模型，默认 `qwen3.8-flash` |
| `FA_PROPOSAL_MODEL` | 否 | 长文方案模型，默认 `qwen-max` |
| `FA_EXTRACT_MODEL` | 否 | 结构化抽取模型，默认 `qwen3.8-flash` |
| `DASHSCOPE_HTTP_BASE_URL` | 否 | DashScope OpenAI 兼容端点，默认官方地址 |
| `FIELDWORK_ASSETS_DIR` | 否 | Tier S 私有资产目录（见下） |
| `FA_STORAGE` | 否 | 存储后端：`json`（默认）/ `sqlite`，未知值安全回落 json（当前仅 Web 宿主与 MCP Server 生效，CLI 待接入） |

### 配置优先级（`kernel/config.py`）

1. 环境变量初始化；
2. 若存在 `~/.fieldwork_agent/config.json`（Windows：`C:\Users\<用户名>\.fieldwork_agent\config.json`，注意不在项目目录内）则**覆盖**环境变量（DashScope/FlyAI/高德密钥、三槽模型、钉钉配置；高德键为 `amap_api_key`，Web 面板键为 `amap_js_key`/`amap_js_security`）；
3. FlyAI 密钥：环境变量 `FLYAI_API_KEY` 优先，为空时回退 `~/.flyai/config.json`。

设置 FlyAI 正式 Key（CLI 无 `login` 命令，体验模式无需 Key）：

```bash
flyai config set key <YOUR_API_KEY>     # 写入 ~/.flyai/config.json
```

### 数据与资产目录

- **项目数据**：`~/.fieldwork_agent/projects/<id>/`（state.json、chat_history.jsonl、knowledge/、search_cache/、output/proposal_*.md）。
- **提示词资产三级体系**（`kernel/assets.py` 探测链，先命中先用）：
  1. 环境变量 `FIELDWORK_ASSETS_DIR` 指向目录；
  2. `<repo>/secrets/`（Tier S 私有完整版，独立私有仓库，被主仓 `.gitignore` 忽略）；
  3. `~/.fieldwork_agent/assets/`；
  4. 内置 Tier P 公开简版（默认）。

  覆盖为整体替换、不做深层合并；坏 JSON / 结构校验失败会抛 `AssetsError`。泄露检查：`python tools/check_secrets_leak.py`。

## 常见问题

| 现象 | 原因与处理 |
|------|-----------|
| Windows 终端报 `UnicodeEncodeError` | 中文 Windows 控制台默认 GBK。CLI 入口已自动切 UTF-8（`_ensure_utf8_stdio`），若自定义脚本复现，参照其实现处理 |
| 管道喂 stdin 启动失败 | prompt_toolkit 需要真实 TTY，属交互库限制非 bug；验证启动逻辑请用真实终端，或对单元逻辑写 pytest |
| PowerShell 拒绝加载 `flyai.ps1` | 改用 `flyai.cmd`，或 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| 对话无回复 / 日志报 LLM 错误 | `DASHSCOPE_API_KEY` 未配置或填错 |
| 机票/酒店搜索不可用 | 未配置 FlyAI 密钥或未装 flyai CLI，属预期降级（其余功能正常） |
| 接口返回 401 `app_token_required` | 服务端设置了 `FIELDWORK_APP_TOKEN` 但请求未带/带错 `X-App-Token` 头 |
| Web 页面 502 或无前端 | `web/dist` 未构建——`cd web && npm run build` 后重启 |
| `/ready` 返回 `not_ready` | 按响应 `checks` 定位：`flyai_cli=false` 装 CLI；`projects_dir_writable=false` 查目录属主 |
| 公网访问连不上但本机 `/health` 通 | 云防火墙/安全组未放行 2143 端口 |
| 路径传 `~/...` 不生效 | 程序不自动展开 `~`，请用绝对路径 |

## 已知限制

1. **搜索覆盖范围**：机票支持国内+国际；酒店/POI/语义搜索覆盖海外主流目的地与港澳台；**火车限境内 + 港澳直通**（如香港西九龙）。海外偏远田野点（如村级）无住宿供给时，Agent 会如实声明并给出"枢纽城市 + 当地学术接待"二段式方案，不虚构供给。地理底座（高德）仅覆盖境内，境外田野点的定位/周边/接驳段落自动降级跳过。
2. **FlyAI 体验模式**：无正式 Key 时可正常搜索但价格脱敏（如 ¥5x）、结果受限。
3. **方案体例**：当前输出为英美式八章体例；高校开题、国家社科活页体例尚未支持（规划中）。方案为草稿，投稿/开题前须人工核校——缺数据处如实标记"待核验"，不虚构参考文献。
4. **Tier S 资产默认不在部署包内**：线上默认使用内置 Tier P 公开简版提示词/访谈库，功能完整但提示词为简版。
5. **测试盲区（Roadmap 未完成项）**：钉钉同步（`sync dingtalk`）的测试覆盖尚未建立；流式输出和五阶段主流程已有 mock 测试覆盖。
6. **单人 LLM 决策**：工具决策依赖宿主 LLM function calling（无关键词路由兜底），模型不可用时工具调用链路整体降级。
7. **数据本地化**：项目数据仅存本地 `~/.fieldwork_agent/`；当前无登录/账号体系（账号与身份一期在规划中，项目级 `owner` 归属已在运行时透传），公网部署须自行配合 `FIELDWORK_APP_TOKEN` 与防火墙策略。
