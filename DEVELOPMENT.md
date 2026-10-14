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
| LLM 对话 | `pip install dashscope`（已含在依赖里） | 降级为关键词规则回复 |
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
├── conftest.py              # FakeConfig + project / make_engine 等 fixture
├── test_config.py           # 三级加载优先级：env → config.json → ~/.flyai
├── test_state.py            # 阶段流转 / 持久化 / 知识库 / 缓存 / 提案 / 溢出
├── test_dialogue_engine.py  # 工具决策 / 阶段推进 / 地点抽取 / system prompt
└── test_flyai_tools.py      # 8 工具 argv 拼装 + 子进程异常分支
```

约定：
- **隔离**：涉及文件系统的测试用 `tmp_path`；`conftest.FakeConfig` 把项目目录指向临时路径，
  绝不读写真实 `~/.fieldwork_agent`。`test_config.py` 通过 monkeypatch
  `CONFIG_DIR` / `PROJECTS_DIR` / `pathlib.Path.home` 隔离用户主目录。
- **不碰网络/子进程**：`flyai_tools` 测试 monkeypatch `_run_command`（测拼装）
  或 `subprocess.run`（测异常分支）；`dashscope` 从不在测试里真实调用。
- **新增用例原则**：优先针对纯逻辑函数（`decide_tool_calls`、`_extract_locations_from_message`、
  `advance_phase`）写断言，行为可预测、无外部依赖。

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

```
CLI (core/cli.py)
  └─ FieldAgent (core/orchestrator.py)  ← 组装下列组件
       ├─ DialogueEngine (core/dialogue_engine.py)   四阶段状态机 + 工具决策
       ├─ FlyAITools (tools/flyai_tools.py)          subprocess → flyai CLI（8 工具）
       ├─ DashScopeChat (tools/dashscope_tools.py)   Generation.call（qwen）
       └─ ProjectState (core/state.py)               JSON 持久化 + 知识库/缓存/提案
```

依赖方向单向：`cli → orchestrator → {dialogue_engine, state, tools}`；`tools` 不回依赖 `core`。
`Config` 为无状态底座，被各层读取。

**四阶段流程**（`ProjectState.PHASES_ORDER`）：
`research_positioning → field_site_profiles → itinerary_planning → proposal_generation → finished`
阶段推进条件见 `DialogueEngine.process_user_message`（定位阶段满 4 个必填字段自动进阶；
画像阶段有 `confirmed` 田野点进阶）。

**持久化布局**（每个项目在 `~/.fieldwork_agent/projects/<id>/`）：
`state.json` · `chat_history.jsonl` · `knowledge/*.json` · `search_cache/*.json` · `output/proposal_*.md`。

---

## 5. 扩展点

### 5.1 新增/修改一个 FlyAI 工具
1. 在 `tools/flyai_tools.py` 加方法，拼装 `args = [...]` 后调用 `self._run_command(args)`；
   命令名/参数名须与 `flyai <command> --help` 完全一致。
2. 在 `tests/test_flyai_tools.py` 补一条 argv 拼装断言。
3. 如需在决策中使用，在 `DialogueEngine.decide_tool_calls` 增加对应调用项。

### 5.2 新增意图关键词
在 `decide_tool_calls` 的关键词分支（航班/火车/住宿/POI/补充/AI/区域/万豪/套餐）里
扩充 `*_keywords` 列表，并补一条对应测试。

### 5.3 新增访谈阶段
1. 在 `ProjectState` 增加 `PHASE_*` 常量并插入 `PHASES_ORDER`；
2. 在 `DialogueEngine.SYSTEM_PROMPTS` / `GUIDING_QUESTIONS` 补该阶段内容；
3. 在 `process_user_message` 的推进判断里加该阶段的进阶条件；
4. 更新 `cli.py` 的 `phase_names` 展示映射。

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
  或直接对 `core`/`tools` 单元逻辑写 pytest。
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
- [x] 单元测试 harness（state / dialogue / flyai / config）+ CI
- [x] Windows UTF-8 输出修复
- [ ] `orchestrator` 与 `dashscope_tools` 的流式输出测试（需 mock `Generation`）
- [ ] `flyai_tools` 组合策略方法（`plan_field_site_access` 等）的分支测试
- [ ] 集成测试：以 fake flyai stub 跑通四阶段端到端
- [ ] `proposal_generation` 的结构化输出模板与质量校验
- [ ] 钉钉同步（`sync dingtalk`）实现与测试
