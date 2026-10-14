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
├── test_guidance.py          # 五阶段常量/提示词、check_advance 推进判定、extract 纯函数
├── test_models_registry.py   # 包络/模型与 registry 能力元数据契约
├── test_registry_surface.py  # 生产态能力面：裸导入即注册 13 项 + registry handler 统一包络
├── test_sources_flyai.py     # 8 搜索原语 argv 拼装 + 异常折进包络
├── test_itinerary.py         # compose_itinerary 聚合（legs/stays/budget coverage）
├── test_site_profile.py      # profile_field_site 聚合分支
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

`Config`（[`core/config.py`](core/config.py)）的加载优先级：

- **DashScope / 模型**：环境变量初始化 → 若存在 `~/.fieldwork_agent/config.json` 则**覆盖** env。
  键：`DASHSCOPE_API_KEY`、`FA_CHAT_MODEL`(默认 `qwen-max`)、`FA_SYSTEM_MODEL`(默认 `qwen-turbo`)。
- **FlyAI**：环境变量 `FLYAI_API_KEY` 优先；缺失时回退读取 `~/.flyai/config.json` 的 `key`。
- **能力开关**：`dashscope_available` / `flyai_available` / `dingtalk_available` 供上层降级判断。

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
       ├─ kernel/aggregation/               profile_field_site / compose_itinerary 本地聚合
       ├─ kernel/documents/                 compose_proposal / compose_interview_guide / suggest_informants
       ├─ kernel/guidance/                  五阶段常量·提示词·check_advance + extract 纯函数
       ├─ kernel/repository/                ProjectRepository 协议 + JsonRepository 持久化
       ├─ kernel/models.py                  共享 pydantic 契约：Envelope/ToolError/SearchItem/
       │                                    ResearchContext/ProposalDoc/BudgetBreakdown…（全内核复用）
       └─ kernel/llm.py                     LLMClient（DashScope OpenAI 兼容端点，function calling）
```

测试夹具（fixtures）：全部集中在 `tests/conftest.py` —— `FakeConfig`（tmp 目录底座）、
`fake_llm`（ScriptedLLM 形状，按脚本返回 chat 响应并记录 messages）、`fake_flyai`（StubClient
形状，8 原语恒返单条目成功包络并记录调用）；宿主/聚合测试一律注入这两个桩，不打真实网络。

依赖方向单向：`cli → orchestrator → {runtime, kernel}`；`runtime` 只 import `kernel`；
**`kernel` 绝不反向 import `core/`、`runtime` 或任何宿主**（只依赖标准库 + pydantic，无状态、
不编排 LLM）。工具决策由宿主 LLM function calling 完成——宿主把 registry 生成的 schema 交给
模型，将其 `tool_calls` 经 `client_kernel_dispatch` 分派回内核能力。
`Config` 为无状态底座，被 `core` 层读取。

**五阶段流程**（`kernel/guidance/phases.PHASES`）：
`research_positioning → field_site_profiles → itinerary_planning → proposal_generation → finished`
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
   （`client` = 注入 FlyAIClient，`llm` = 注入 LLMClient）。

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
- [ ] `orchestrator` 与 `kernel/llm` 的流式输出测试（需 mock `Generation`）
- [ ] 集成测试：以 fake flyai stub 跑通五阶段端到端
- [ ] 钉钉同步（`sync dingtalk`）的测试覆盖
- [ ] MCP Server / Web 宿主（后续计划：复用 registry schema 与 kernel 能力面）
