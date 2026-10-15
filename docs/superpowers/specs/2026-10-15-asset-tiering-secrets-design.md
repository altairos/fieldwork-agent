# 技术秘密分级剥离设计：prompt/示范库开源前剥离

- 日期：2026-10-15
- 状态：设计已获用户批准（四个关键分叉见 §2），待实施
- 关联：专利评估结论（软著优先 + 核心资产以技术秘密保护）、比赛要求开源的硬约束

## 0. 背景与目标

专利评估结论：本项目核心价值在领域知识工程化（示范库、方法论、提示词体系）与架构工程质量，单点算法不足以支撑发明专利；优先保护路径为 **软著 + 技术秘密**。因 OPC 比赛要求开源，开源仓库不能携带高价值 prompt 资产，且这些资产已存在于全部 git 历史中并已推送远程——单纯删文件无效。

目标：

1. 开源仓库功能完整可跑（公开版资产），质量合理降级；
2. 完整版资产（Tier S）永不进入开源 git 历史（含重写后的历史）；
3. 比赛/演示环境挂载私有资产包即恢复完整能力；
4. 时序上先完成软著登记（完整版材料不构成公开），再开源。

## 1. 资产盘点（现状）

进 git 的技术秘密资产（4 个文件，约 600 行）：

| 资产 | 位置 | 性质 |
|---|---|---|
| `SYSTEM_PROMPTS` 五阶段完整提示词 | `kernel/guidance/phases.py` L41-111 | 对话引导 prompt |
| `GUIDING_QUESTIONS` 引导问题 | `kernel/guidance/phases.py` L114-146 | 功能骨架（定级见 §3） |
| `INFORMANT_LIBRARY` 11 类完整词条 | `kernel/guidance/informants.py` L22-409 | 领域知识资产（最高价值） |
| `INTERVIEW_GUIDE_METHODOLOGY` 完整方法论 | `kernel/guidance/informants.py` L412-461 | 领域知识资产（最高价值） |
| `INFORMANT_EXAMPLES` few-shot 锚（派生） | `kernel/guidance/informants.py` L492-494 | 生成质量核心 |
| `_EXAMPLE_TYPES` 精选键 | `kernel/guidance/informants.py` L467 | 派生配置 |
| suggest/guide 的 system prompt 措辞 | `kernel/documents/informants.py` L90-93、`kernel/documents/proposal.py` L382-383 | 调优措辞 |

引用面：`web_host/agent/context.py`（经 `phase_prompt` 间接）、`mcp_server/prompts.py`（经 `phase_prompt`）、`mcp_server/resources.py`（直引 `INFORMANT_EXAMPLES`/`INTERVIEW_GUIDE_METHODOLOGY`）、`kernel/documents/informants.py`、`kernel/documents/proposal.py`、`tests/test_guidance.py` L221/L242-252（形状断言）。

## 2. 决策记录（用户已确认）

| 分叉 | 决策 |
|---|---|
| 剥离机制 | **分级剥离**：开源带新写精简公开版，完整版运行时覆盖加载 |
| 仓库策略 | **单仓库历史重写**（git filter-repo），保留提交历史 |
| 执行时机 | **现在就改造**：立即执行代码改造，历史重写作为开源前预案 |
| 比赛约束 | **比赛要求开源**：历史重写有硬截止（比赛提交前） |

## 3. 资产三级清单

### Tier S — 技术秘密（移入 `secrets/`，永不进开源 git）

| 资产 | 私有包去向 |
|---|---|
| `INFORMANT_LIBRARY` 完整 11 类词条 | `secrets/informant_library.json` |
| `_EXAMPLE_TYPES` 精选 6 类键 | `secrets/example_types.json` |
| `INTERVIEW_GUIDE_METHODOLOGY` 完整版 | `secrets/interview_methodology.txt` |
| `SYSTEM_PROMPTS` 五阶段完整版 | `secrets/system_prompts.json` |
| suggest/guide 的 system prompt 完整措辞 | `secrets/generation_prompts.json` |

### Tier P — 公开版资产（新写精简，替换进开源代码）

编写要求：

- **示范库**：新写 3 类教学级词条（建议村干部、乡村教师、返乡务工者的简化版）。必须从零新写措辞，**不得**从私有版抽取——避免泄露私有版文风、颗粒度与领域深度。
- **方法论**：新写约 1/3 篇幅通用版。保留半结构式骨架（开场/主问/追问基础/知情同意要点），剥离追问五技、敏感话题三步法、可照读知情同意话术原文。
- **阶段提示词**：每阶段新写 5-8 句精简版。保留职责、字段采集要求、工具指引与红线约束（如"参考文献不得虚构"是功能约束，必须开源）。
- **措辞差异化**：公开版的标题与标志性短语刻意与私有版不同（如方法论标题用"访谈方法要点（开源简版）"而非"半结构式访谈方法论要点"），使历史重写的特征串识别无歧义（见 §9）。

### Tier O — 保持现状开源，不动

- `GUIDING_QUESTIONS`、`REQUIRED_FIELDS`、`PHASES`、`MIN_POSITIONING_ANSWERED` 与 `check_advance` 状态机、`match_examples` 二字组算法、全部聚合算法（专利评估确认为常规工程手段，是开源核心卖点）；
- `PROPOSAL_SECTIONS` 八章标题（项目说明书已公开）、`_TOOL_GUIDANCE`；
- `documents/informants.py` `_user_prompt` 的输出契约段（"JSON 数组、4-6 类、七字段"——开源版运行必需的接口契约）。

## 4. assets 加载器设计（`kernel/assets.py` 新建）

### 探测链（呼应项目密钥三级兜底惯例）

```
FIELDWORK_ASSETS_DIR 环境变量
  → <repo>/secrets/
  → ~/.fieldwork_agent/assets/
  → 无（纯公开版运行）
```

### 语义

- **按资产键整体替换**：secrets 中存在的键整体生效，缺失的键用公开版。不做深层 merge——简单、可预测、无半吊子状态。
- **fail-fast**：资产文件存在但解析失败 → 启动抛 `AssetsError`（含文件路径与原因）。防止"以为在用完整版、实则静默降级"。
- **深拷贝**：访问器一律返回深拷贝（对齐 `guidance/informants.get_informant` 现有惯例，宿主改动不污染资产）。
- **惰性加载 + 进程内缓存 + `reload(assets_dir=None)` 钩子**（供测试注入 tmp 目录）。首次访问触发加载，`threading.Lock` 防并发。

### 接口（概念）

```python
def informant_library() -> dict[str, dict]      # 键为类型名，值为八字段词条
def example_types() -> list[str]                 # 精选键（公开版为 3 键）
def interview_methodology() -> str
def system_prompts() -> dict[str, str]           # phase → 提示词
def generation_prompt(key: str) -> str           # suggest_system / guide_system / guide_instruction
def reload(assets_dir: Path | None = None) -> None
```

## 5. 引用点改造清单

| 文件 | 改动 |
|---|---|
| `kernel/guidance/informants.py` | 重构：删除 11 类词条与完整方法论常量 → 内置 3 类公开版数据；`get_informant`/`match_examples`/`_to_archive`/`_bigrams` 逻辑不动，数据源改为经 assets 取 |
| `kernel/guidance/phases.py` | `SYSTEM_PROMPTS` → 公开精简版；`phase_prompt()` 经 assets 取（含覆盖） |
| `kernel/documents/informants.py` | `_user_prompt` 的 few-shot 注入与 system 措辞经 assets 取 |
| `kernel/documents/proposal.py` | `_guide_prompt`/`_guide_degrade`/`_entry_block` 的方法论与条目、guide system 措辞经 assets 取 |
| `mcp_server/resources.py` | 资源内容经 assets 取；资源键集合契约不变 |
| `web_host/agent/context.py` | 无需改（经 `phase_prompt` 间接引用） |

开源版行为：开箱即用、功能完整、质量降级（3 类示范、通用方法论）；挂 secrets 目录恢复完整能力。比赛/演示环境挂私有包。

## 6. secrets 目录与私有版本管理

```
secrets/
  informant_library.json
  example_types.json
  interview_methodology.txt
  system_prompts.json
  generation_prompts.json
  feature-strings.txt     # 私有版独有特征串清单（历史重写验证用，见 §9）
  README.md               # 自说明：技术秘密资产，勿提交勿分发
```

- `secrets/` 内部 `git init`，推送到独立 GitHub 私有仓库（建议名 `fieldwork-agent-secrets`）——防丢失、有修改历史。
- 主仓库 `.gitignore` 增加 `secrets/`。
- 资产文件格式即 §4 接口形状；`example_types.json` 中的键必须 ⊆ `informant_library.json` 的键（加载时校验，违反抛 `AssetsError`）。

## 7. 测试改造（对齐四层测试金字塔）

- **新增 `tests/test_assets.py`（L1）**：
  - 无 env、无目录 → 访问器返回公开版；
  - `tmp_path` 假包注入（经 `reload`）→ 覆盖生效、整体替换语义（缺键用公开版）；
  - 坏 JSON → `AssetsError`；`example_types` 键越界 → `AssetsError`；
  - 深拷贝防污染；`reload()` 后状态隔离。
- **改造 `tests/test_guidance.py`**：L221 方法论断言、L242-252 示范库形状断言改为公开版形状（3 条、⊆ 公开库）；`conftest.py` 增加 autouse fixture：清除 `FIELDWORK_ASSETS_DIR` 并 `reload()`（防本地 secrets 泄入测试断言）。
- **MCP resources/prompts 测试**：资源键集合断言保留；内容断言改基于公开版。
- **降级路径**：公开版 3 条下，无 llm 的 `match_examples` 输出满足 1-6 类契约（上限 3 条）。
- **L3 宿主同构契约**不动（schema 层不涉及资产内容）。
- **行为评估集**（`evals/scenarios/`，非 CI 门槛）：secrets 资产每次变更后手动回归，防止私有版调优引入行为回退。

## 8. CI 与防误提交

- `.gitignore` 增加 `secrets/`。
- `.github/workflows/ci.yml` 增加 "secrets-leak guard" 步骤：
  1. `git ls-files` 输出不含 `secrets/` 路径；
  2. tracked 的 `.py/.json/.md` 中抽查私有版特征串（清单以最小子集硬编码于 CI 脚本，完整清单存 secrets 仓库）。
- 开发工作流约定（写入 `DEVELOPMENT.md` 新章节"资产分级与 secrets 维护"）：
  - **公开版只降不升**：新调优措辞先进私有版；公开版仅同步功能性必要变更（字段/契约变化）；
  - 修改私有资产 → secrets 仓库 commit + 跑行为评估集；
  - 新增能力若引入新 prompt 资产 → 同时定义公开版默认与私有覆盖键。

## 9. 历史重写预案（开源前执行；硬截止 = 比赛提交）

前提：M2 软著申请已提交（登记完整版材料）。

1. **备份**：`git clone --mirror` 离线存档（私有介质）；确认 secrets 私有仓库为最新。
2. **特征串清单**：`secrets/feature-strings.txt` 列出私有版独有字符串（各私有词条正文独有长句、方法论完整版标题与独有段落、SYSTEM_PROMPTS 完整版特征句等；裸键与 <12 字短串已弃用，避免误伤通用词）。公开版措辞已差异化（§3），识别无歧义。
3. **副本演练**（前置检查：重写后、force push 前必须验证——重写后 HEAD 及全历史对 `secrets/feature-strings.txt` 清单逐条零命中，`tools/check_secrets_leak.py` 自身的哨兵字面量除外；不满足则中止演练、不得进入后续步骤）：
   - `pip install git-filter-repo`；
   - 在 mirror 副本上执行 `git filter-repo --blob-callback`：blob 数据命中特征串集合 → 整体替换为对应公开版文件内容（替换映射由预案脚本维护）；
   - 验证一：`git rev-list --all` 全历史 `git grep` 特征串清单 → 零命中；
   - 验证二：`git log --all -S "<特征串>" --oneline` → 空；
   - 验证三：重写后 clone 全量测试跑通。
4. **正式执行**：清理 `.worktrees/` → 对真仓库执行重写 → `git push --force --all` 与 `--tags`。
5. **GitHub 侧**：转 public 前联系 GitHub Support 清理悬挂 commit 对象（旧 SHA 直访残留）；确认无 fork。
6. **善后**：重建 worktrees（独立 venv，见既有 worktree 规范）；所有本地 clone 作废重新拉取；协作者同步。

演练与正式执行之间预留至少一周缓冲。

## 10. 里程碑时序

| 里程碑 | 内容 | 前置 |
|---|---|---|
| **M0（现在）** | 本设计落地：assets 加载器、分级资产、引用点改造、测试、CI 防护、secrets 私有仓库初始化 | — |
| **M1** | V1.0 功能冻结 | M0 |
| **M2** | 软著申请：登记完整版（鉴别材料含 assets 架构与资产文件代码页；材料不构成公开；受理即获登记号，无需等证书） | M1 |
| **M3** | 开源演练：mirror 副本重写 + 三重验证全绿 | M2 已提交 |
| **M4** | 正式重写 + force push + 仓库转 public | M3 验证全绿，距 M3 ≥1 周，早于比赛提交 |

## 11. 风险清单

| 风险 | 缓解 |
|---|---|
| filter-repo 多版本 blob 漏洗 | 特征串清单全覆盖 + 措辞差异化设计 + 副本演练三重验证 |
| GitHub 旧 commit SHA 残留可直访 | 转 public 前联系 Support 清理；知晓 SHA 才能访问，接受残余风险并记录 |
| secrets 丢失 | 私有伴生仓库 + mirror 备份 |
| secrets 误提交主仓库 | `.gitignore` + CI secrets-leak guard |
| 公开版质量过低损害开源形象 | 教学级精心撰写而非残缺级；行为评估集覆盖公开版冒烟 |
| 双版本行为漂移 | 私有版变更跑行为评估集；契约测试双模式（公开/注入假包） |
| 比赛开源条款细节变化 | M4 前复核 OPC 规则原文 |

## 12. 验收标准（M0）

1. 全量测试绿（公开版模式，无 env 无 secrets 目录）；
2. `FIELDWORK_ASSETS_DIR` 指向真 secrets 包时，`INFORMANT_LIBRARY` 11 类、完整方法论、完整提示词生效（手动检查 + 行为评估集冒烟）；
3. `git status` 干净，`secrets/` 未被跟踪，CI 全绿（含 secrets-leak guard）；
4. secrets 私有仓库已建立并推送首个 commit；
5. `DEVELOPMENT.md` 含资产分级维护章节。

（M3 验收：演练重写的三重验证全绿。M4 验收：开源仓库全历史特征串零命中 + 转 public 成功。）

## 13. M0 实施任务分解（供实现计划展开）

1. `kernel/assets.py` + `tests/test_assets.py`（TDD：先测后实现）；
2. 编写 Tier P 公开版资产（3 词条、简版方法论、5 阶段简版提示词、公开版 generation 措辞；注意措辞差异化）；
3. 重构 `kernel/guidance/informants.py` 接 assets（公开版内置 + 数据源切换）；
4. 改造 `kernel/guidance/phases.py`（公开版 SYSTEM_PROMPTS + `phase_prompt` 经 assets）；
5. 改造 `kernel/documents/informants.py`、`kernel/documents/proposal.py`（few-shot 注入、方法论、guide 措辞经 assets）;
6. 改造 `mcp_server/resources.py`（资源内容经 assets）；
7. 生成 secrets 资产包（从现完整版导出五文件 + feature-strings.txt + README）并初始化私有仓库；
8. `.gitignore` + CI secrets-leak guard；
9. 测试全量改造与回归（§7 清单）；
10. `DEVELOPMENT.md` 增补章节；文档与本 spec 归档。
