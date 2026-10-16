# 资产分级与 secrets 剥离（M0 改造）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把私有 prompt/示范资产从开源主仓剥离为"Tier P 公开版内置 + Tier S secrets 目录覆盖"的分级结构，主仓测试全绿且不泄漏任何私有特征串。

**Architecture:** 新增 `kernel/assets.py` 作为唯一资产入口：内置公开版资产（Tier P），运行时按探测链（env → `<repo>/secrets/` → `~/.fieldwork_agent/assets/` → 内置）加载 Tier S 覆盖包，按资产键整体替换、fail-fast 校验。`guidance/informants.py`、`guidance/phases.py` 删除私有常量，改经 assets 动态取值并用 PEP 562 `__getattr__` 保持属性访问兼容；documents 与 mcp_server 消费方同步切换。CI 增加 secrets 泄漏 guard。

**Tech Stack:** Python 3.11+（PEP 562 模块级 `__getattr__`）、pytest、标准库（json/os/threading/pathlib/copy），无新第三方依赖。

**Spec:** `docs/superpowers/specs/2026-10-15-asset-tiering-secrets-design.md`（commit 93af96b）

## Global Constraints

- Python ≥ 3.11；**不新增任何第三方依赖**。
- `kernel/assets.py` 是唯一资产入口。Tier S 探测链（先命中先用）：环境变量 `FIELDWORK_ASSETS_DIR` → `<repo>/secrets/` → `~/.fieldwork_agent/assets/` → 内置公开版。
- 覆盖按**资产键整体替换**（无深层 merge）：覆盖包提供的文件整体生效，未提供的资产回落公开版。
- env 指向的目录不存在、显式 reload 的目录不存在、坏 JSON/编码错误、结构校验失败 → 一律 fail-fast 抛 `AssetsError`，消息含出错文件路径。
- 公开版（Tier P）措辞与私有版（Tier S）**刻意差异化**：标题、结尾句、标志性短语不得相同（供 M3 `git filter-repo` blob 回调特征串识别）。
- `secrets/` 目录永不入主仓：`.gitignore` 忽略 + CI guard（`tools/check_secrets_leak.py`）双保险。
- Tier O 常量不动：`GUIDING_QUESTIONS`、`REQUIRED_FIELDS`、`check_advance`、`match_examples` 算法、`PROPOSAL_SECTIONS`、各 user prompt 的输出契约段。
- 测试全 mock：不联网、不调 subprocess、无 key。
- **时序红线**：secrets 资产包导出（Task 2）必须先于常量删除（Task 3+）——导出脚本依赖旧常量。
- 当前分支 `feat/web-frontend`；每个任务结束时工作目录必须干净（commit 完毕）。

## 文件结构总览

| 文件 | 操作 | 职责 |
|---|---|---|
| `kernel/assets.py` | 新建 | 资产加载器：Tier P 内置数据 + Tier S 探测/缓存/校验 + 六个访问器 |
| `tests/test_assets.py` | 新建 | 加载器契约测试（公开版默认/整体替换/回落/fail-fast/深拷贝） |
| `tests/conftest.py` | 修改（末尾追加） | autouse fixture：全部测试运行在公开版资产下 |
| `secrets/export_from_legacy.py` | 新建（不入主仓） | 一次性迁移：从旧常量导出 Tier S 五文件 + feature-strings.txt |
| `secrets/README.md` | 新建（不入主仓） | 私有资产包结构/生效方式/维护约定 |
| `.gitignore` | 修改 | 追加 `secrets/` |
| `kernel/guidance/informants.py` | 修改 | 删四数据常量 → `__getattr__` 动态 + `get_informant`/`match_examples` 改走 assets |
| `kernel/guidance/phases.py` | 修改 | 删 `SYSTEM_PROMPTS` → `__getattr__` 动态 + `phase_prompt` 改走 assets |
| `kernel/documents/informants.py` | 修改 | suggest system 提示词改走 `assets.generation_prompt` |
| `kernel/documents/proposal.py` | 修改 | guide system/instruction 改走 `assets.generation_prompt` |
| `mcp_server/resources.py` | 修改 | from-import 改属性访问（动态生效） |
| `tests/test_guidance.py` | 修改 | 7 处断言改公开版（"长老"→"村干部"等） |
| `tests/test_documents.py` | 修改 | 4 处断言改公开版 |
| `tests/test_mcp_resources_prompts.py` | 修改 | L23 "长老"→"村干部" |
| `tests/test_assets_override.py` | 新建 | 覆盖端到端：假包切换后全链路动态生效 |
| `tools/check_secrets_leak.py` | 新建 | CI guard：secrets/ 未跟踪 + 特征串零命中 |
| `.github/workflows/ci.yml` | 修改 | pytest 后追加 guard 步骤 |
| `DEVELOPMENT.md` | 修改 | 文末追加"## 10. 资产分级与 secrets 维护" |

---

### Task 1: kernel/assets.py 资产加载器（Tier P 内置 + Tier S 覆盖）

**Files:**
- Create: `kernel/assets.py`
- Test: `tests/test_assets.py`
- Modify: `tests/conftest.py`（文件末尾追加 autouse fixture）

**Interfaces:**
- Consumes: 无（首个任务）
- Produces: 模块 `fieldwork_agent.kernel.assets`——
  - `AssetsError(RuntimeError)`：消息含出错文件路径
  - `reload(assets_dir: Path | None = None) -> None`：显式指定覆盖目录；`None` 恢复自动探测
  - `informant_library() -> dict[str, dict]`（键=词条 type，值含八字段）
  - `example_types() -> list[str]`（生效清单，键必须 ⊆ 生效库）
  - `interview_methodology() -> str`
  - `system_prompts() -> dict[str, str]`（五阶段键齐全且值为非空 str）
  - `generation_prompt(key: str) -> str`（key ∈ {suggest_system, guide_system, guide_instruction}，未知键 ValueError）
  - 环境变量 `FIELDWORK_ASSETS_DIR`

- [ ] **Step 1: 写失败测试 `tests/test_assets.py`（全文）**

```python
"""kernel/assets.py 加载器契约：公开版默认、整体替换、回落、fail-fast、缓存与深拷贝。"""
import json

import pytest

from fieldwork_agent.kernel import assets

PHASE_KEYS = [
    "research_positioning", "field_site_profiles", "itinerary_planning",
    "proposal_generation", "finished",
]
GENERATION_KEYS = ["suggest_system", "guide_system", "guide_instruction"]

FAKE_LIBRARY = {
    "测试甲": {"type": "测试甲", "approach": "甲式接触", "cautions": "甲注意",
             "question_domains": ["域一"], "ethics": "甲伦理",
             "access_channel": "甲渠道", "preparation": "甲准备",
             "examples": ["问一", "问二", "追问：三"]},
    "测试乙": {"type": "测试乙", "approach": "乙式接触", "cautions": "乙注意",
             "question_domains": ["域二"], "ethics": "乙伦理",
             "access_channel": "乙渠道", "preparation": "乙准备",
             "examples": ["问一", "问二", "追问：三"]},
}


@pytest.fixture(autouse=True)
def empty_dir(tmp_path):
    """每个测试固定运行在空覆盖目录（公开版）下，收尾同样复位。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    assets.reload(empty)
    yield empty
    assets.reload(empty)


def _write_pkg(root, *, library=None, types=None, methodology=None,
               prompts=None, generation=None):
    """写入部分覆盖包：None 表示不写该文件（该资产回落公开版）。"""
    root.mkdir(parents=True, exist_ok=True)
    if library is not None:
        (root / "informant_library.json").write_text(
            json.dumps(library, ensure_ascii=False), encoding="utf-8")
    if types is not None:
        (root / "example_types.json").write_text(
            json.dumps(types, ensure_ascii=False), encoding="utf-8")
    if methodology is not None:
        (root / "interview_methodology.txt").write_text(
            methodology, encoding="utf-8")
    if prompts is not None:
        (root / "system_prompts.json").write_text(
            json.dumps(prompts, ensure_ascii=False), encoding="utf-8")
    if generation is not None:
        (root / "generation_prompts.json").write_text(
            json.dumps(generation, ensure_ascii=False), encoding="utf-8")
    return root


def test_public_defaults(empty_dir):
    lib = assets.informant_library()
    assert set(lib) == {"村干部", "乡村教师", "返乡务工者"}
    for entry in lib.values():
        assert set(entry) == {"type", "approach", "cautions", "question_domains",
                              "ethics", "access_channel", "preparation", "examples"}
    assert assets.example_types() == ["村干部", "乡村教师", "返乡务工者"]
    assert assets.interview_methodology().startswith("# 访谈方法要点")
    prompts = assets.system_prompts()
    assert set(prompts) == set(PHASE_KEYS)
    for key in GENERATION_KEYS:
        assert assets.generation_prompt(key)


def test_full_package_replace(tmp_path):
    pkg = _write_pkg(
        tmp_path / "pkg", library=FAKE_LIBRARY, types=["测试甲"],
        methodology="# 假方法论",
        prompts={k: f"假-{k}" for k in PHASE_KEYS},
        generation={k: f"假生成-{k}" for k in GENERATION_KEYS})
    assets.reload(pkg)
    assert set(assets.informant_library()) == {"测试甲", "测试乙"}
    assert assets.example_types() == ["测试甲"]
    assert assets.interview_methodology() == "# 假方法论"
    assert assets.system_prompts()["itinerary_planning"] == "假-itinerary_planning"
    assert assets.generation_prompt("guide_system") == "假生成-guide_system"


def test_partial_fallback_to_public(tmp_path):
    pkg = _write_pkg(tmp_path / "pkg", library=FAKE_LIBRARY, types=["测试乙"])
    assets.reload(pkg)
    assert set(assets.informant_library()) == {"测试甲", "测试乙"}
    # 未覆盖的资产回落公开版
    assert assets.interview_methodology().startswith("# 访谈方法要点")
    assert "田野罗盘" in assets.system_prompts()["research_positioning"]


def test_bad_json_raises(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "informant_library.json").write_text("{broken", encoding="utf-8")
    assets.reload(pkg)
    with pytest.raises(assets.AssetsError, match="informant_library.json"):
        assets.informant_library()


def test_example_types_beyond_library_raises(tmp_path):
    pkg = _write_pkg(tmp_path / "pkg", library=FAKE_LIBRARY,
                     types=["测试甲", "幽灵"])
    assets.reload(pkg)
    with pytest.raises(assets.AssetsError, match="幽灵"):
        assets.example_types()


def test_library_replaced_but_default_types_raises(tmp_path):
    # 只换库不换清单：公开版默认清单三键不在新库 -> 越界 fail-fast
    pkg = _write_pkg(tmp_path / "pkg", library=FAKE_LIBRARY)
    assets.reload(pkg)
    with pytest.raises(assets.AssetsError):
        assets.example_types()


def test_env_dir_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("FIELDWORK_ASSETS_DIR", str(tmp_path / "nope"))
    assets.reload(None)  # 恢复自动探测，env 目录不存在必须抛
    with pytest.raises(assets.AssetsError, match="FIELDWORK_ASSETS_DIR"):
        assets.informant_library()


def test_reload_explicit_missing_dir_raises(tmp_path):
    assets.reload(tmp_path / "nope")
    with pytest.raises(assets.AssetsError, match="nope"):
        assets.informant_library()


def test_deepcopy_isolation(empty_dir):
    lib = assets.informant_library()
    lib["村干部"]["approach"] = "被污染"
    assert assets.informant_library()["村干部"]["approach"] != "被污染"


def test_reload_switches_package(tmp_path):
    pkg_a = _write_pkg(tmp_path / "a", methodology="# 甲方法")
    pkg_b = _write_pkg(tmp_path / "b", methodology="# 乙方法")
    assets.reload(pkg_a)
    assert assets.interview_methodology() == "# 甲方法"
    assets.reload(pkg_b)
    assert assets.interview_methodology() == "# 乙方法"  # 缓存不串包


def test_system_prompts_missing_phase_raises(tmp_path):
    broken = {k: f"假-{k}" for k in PHASE_KEYS if k != "finished"}
    pkg = _write_pkg(tmp_path / "pkg", prompts=broken)
    assets.reload(pkg)
    with pytest.raises(assets.AssetsError, match="finished"):
        assets.system_prompts()


def test_generation_prompt_unknown_key_raises(empty_dir):
    with pytest.raises(ValueError):
        assets.generation_prompt("nope")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_assets.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'fieldwork_agent.kernel.assets'`（收集阶段即失败）

- [ ] **Step 3: 写完整实现 `kernel/assets.py`（全文）**

```python
"""资产分级加载器：Tier P（内置公开版）+ Tier S（目录覆盖）。

Tier S 覆盖包探测链（先命中先用）：
  1. 环境变量 ``FIELDWORK_ASSETS_DIR`` 指向的目录（设置了但不存在 -> AssetsError）
  2. 仓库根下的 ``secrets/`` 目录（存在即用）
  3. ``~/.fieldwork_agent/assets/`` 目录（存在即用）
  4. 都不存在 -> 使用本模块内置的 Tier P 公开版资产

覆盖按资产键整体替换（无深层 merge）：覆盖包里提供的文件整体生效，
未提供的资产回落公开版。坏 JSON / 编码错误 / 结构校验失败一律
fail-fast 抛 :class:`AssetsError`（消息含出错文件路径）。
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOME_ASSETS_DIR = Path.home() / ".fieldwork_agent" / "assets"
_ENV_VAR = "FIELDWORK_ASSETS_DIR"

# 阶段键硬编码于此，避免 import guidance.phases 造成循环依赖。
_PHASE_KEYS = (
    "research_positioning",
    "field_site_profiles",
    "itinerary_planning",
    "proposal_generation",
    "finished",
)
_GENERATION_KEYS = ("suggest_system", "guide_system", "guide_instruction")

_ASSET_FILES = {
    "informant_library": "informant_library.json",
    "example_types": "example_types.json",
    "interview_methodology": "interview_methodology.txt",
    "system_prompts": "system_prompts.json",
    "generation_prompts": "generation_prompts.json",
}


class AssetsError(RuntimeError):
    """资产覆盖包缺失/损坏/校验失败。消息含出错文件路径。"""


# ------------------------------------------------------------------ Tier P 公开版

_PUBLIC_EXAMPLE_TYPES = ["村干部", "乡村教师", "返乡务工者"]

_PUBLIC_INFORMANT_LIBRARY: dict[str, dict] = {
    "村干部": {
        "type": "村干部",
        "approach": (
            "经村两委或驻村干部引荐后正式拜访，先说明来意与匿名原则，"
            "再请其从治理视角介绍村情；对涉及利益纠纷的话题保持中立。"
        ),
        "cautions": (
            "其叙述常带有政绩或维稳导向，注意与普通村民说法交叉验证；"
            "避免在敏感节点（选举、征地）追问站队问题。"
        ),
        "question_domains": [
            "村史与建制沿革", "人口结构与外出务工规模", "集体经济与主要产业",
            "近年的政策落地项目", "村内矛盾调解机制",
        ],
        "ethics": "涉及具体纠纷时做化名处理，不向第三方转述其个人观点。",
        "access_channel": "通过乡镇政府或驻村工作队发函/引荐，或经村内熟人引路登门。",
        "preparation": "备好介绍信与研究简况一页纸；提前了解该村主导产业与近年重点项目。",
        "examples": [
            "您好，我在做关于村庄发展的调查研究，想请您从管理者的角度聊聊村里这些年的变化。",
            "村里近年在推进哪些项目？村民的参与度和反馈如何？",
            "追问：您提到的那个矛盾后来是怎么解决的？",
        ],
    },
    "乡村教师": {
        "type": "乡村教师",
        "approach": (
            "以学校为接触点，课后或家访间隙交谈；教师对社区变迁观察细腻，"
            "适合从教育与代际流动切入。"
        ),
        "cautions": (
            "避免打听在校学生的具体家庭隐私；谈及学校困境时不引导其对管理层作评价。"
        ),
        "question_domains": [
            "生源与留守儿童比例变化", "家长教育观念变迁",
            "教师流动与乡村学校困境", "村庄文化活动的组织", "学生对未来的想象",
        ],
        "ethics": "不记录可识别学生身份的信息；对教师个人职业选择相关内容匿名处理。",
        "access_channel": "经县教育部门或校长同意后到校访谈，或通过家长牵线课后交流。",
        "preparation": "了解当地学校撤并政策背景；准备一两句对当地教育现状的中性开场。",
        "examples": [
            "老师您好，我想了解这十来年村里孩子上学情况的变化，方便聊聊吗？",
            "家长对读书的态度这几年有什么不一样吗？",
            "追问：您说的那个变化，大概是从哪一年开始的？",
        ],
    },
    "返乡务工者": {
        "type": "返乡务工者",
        "approach": (
            "经亲友或村口熟人自然引入，边做活边聊；其对城乡差距与返乡决策"
            "有一手体验，适合从生计史切入。"
        ),
        "cautions": (
            "收入话题敏感，宜问区间不宜问精确数；不在其亲属面前追问打工期间的家庭矛盾。"
        ),
        "question_domains": [
            "外出打工的经历与城市印象", "返乡的原因与决策过程",
            "返乡后的生计安排", "对村庄未来的判断", "子女教育与留守选择",
        ],
        "ethics": "涉及欠薪、工伤等经历时尊重其是否展开；对其提及的雇主信息化名。",
        "access_channel": "经村中小卖部、合作社等日常聚集场合结识，或请村干部推荐近期返乡者。",
        "preparation": "了解当地主要劳务输出地与产业；准备以请教口吻而非访谈腔进入。",
        "examples": [
            "大哥/大姐，听说您前些年在外面上班，后来回来了，我挺想了解这个过程的。",
            "当初回来主要是哪方面的考虑？现在看这个决定怎么样？",
            "追问：您说城里机会多但留不下来，具体是指哪些方面？",
        ],
    },
}

_PUBLIC_METHODOLOGY = """# 访谈方法要点（开源简版）

## 开场
用两三句话自我介绍：是谁、做什么、为何来找 ta。说明匿名与录音许可，征得同意后再录。先从日常话题（家里的收成、村里的近事）进入，不急于抛研究问题。

## 主问
准备 5-8 个开放问题，一次只问一个。用邀请式问法（“能讲讲……吗”），让对方完整叙述，不打断、不抢话。对方跑题时听完再温和引回。

## 追问
对模糊表述追问具体事例：“您说变化很大，能举一件最近的事吗？”数字敏感时可问区间。对方明显回避的话题，换个时间或换种问法再靠近，不硬问。

## 敏感话题
放在关系建立之后、访谈中段以后再触及。先问一般情况，再问个案。对方表现出不愿回答，立即收住并感谢，转向其他话题。

## 知情同意
口头说明研究用途、匿名处理与随时退出的权利，征得同意后开始正式访谈；录音再次单独确认。涉及第三方（家人、邻里）的信息，事后笔记中一律化名。

## 访谈之后
24 小时内补全笔记，标注未覆盖的问题。矛盾说法先如实记录，再通过其他访谈对象交叉验证。资料按匿名规范归档。
"""

_PUBLIC_SYSTEM_PROMPTS: dict[str, str] = {
    "research_positioning": (
        "你是田野罗盘 Agent，为人类学/社会学田野调查新手提供定位辅导。"
        "当前处于“研究定位”阶段。\n\n"
        "请通过温和且有结构的方式，帮助研究者厘清以下信息：\n\n"
        "0. **输出用途**：这份方案主要用在什么场合？（海外/港澳申请、高校开题、"
        "项目申报、课程作业）——先问这一项，它决定后续模板与能力边界。\n"
        "1. **学科取向**：人类学/社会学/政治学/跨学科？理论框架倾向？\n"
        "2. **研究问题**：初步的研究问题或关注焦点，越具体越好。\n"
        "3. **田野点确定程度**：已明确地点 / 有区域范围 / 完全开放由你推荐？\n"
        "4. **调研规模**：单人还是团队？预计调研时长（短期 2-4 周、中期 1-3 月、"
        "长期 3 月以上）？\n"
        "5. **经费来源与额度**：是否有经费支持？大致预算范围？\n"
        "6. **时间窗口**：希望在什么时间段进行调研？是否有硬性截止日期？\n\n"
        "请一次只问 2-3 个问题，不要一次性抛出所有问题。研究者提到模糊概念"
        "（如“少数民族社区”）时，主动帮 ta 细化到可能的具体地点范围。"
        "语气友好务实，像有经验的学长学姐在给建议。"
    ),
    "field_site_profiles": (
        "你是田野罗盘 Agent。当前处于“田野点画像”阶段。\n\n"
        "你已经了解了研究者的基本需求，现在需要利用可用的旅行搜索工具，"
        "为候选田野点构建画像。对于每个候选地点，请从以下维度收集和分析信息：\n\n"
        "1. **交通可达性**：用航班和火车检索了解大交通成本和时间\n"
        "2. **住宿条件**：检索当地民宿和酒店，评估价格范围和适合驻村的选择\n"
        "3. **文化资源**：了解当地的宗教场所、古镇古村、博物馆等文化景点\n"
        "4. **生活配套**：了解当地饮食、市内交通等日常生活信息\n"
        "5. **已有研究基础**：该地是否有其他学者做过研究？可形成学术对话吗？\n\n"
        "请用旅行搜索工具获取真实数据来辅助判断，并将搜索结果与研究者的"
        "自有资料交叉验证。当研究者确认了最终田野点后，再进入下一阶段。"
    ),
    "itinerary_planning": (
        "你是田野罗盘 Agent。当前处于“行程规划”阶段。\n\n"
        "基于已确定的田野点和研究设计，请制定详细的调研行程方案：\n\n"
        "1. **大交通安排**：对比航班和火车的价格、时间、舒适度，选择合适方案\n"
        "2. **小交通衔接**：从交通枢纽到田野点的最后一公里方案\n"
        "3. **住宿匹配**：按调研阶段分配不同住宿（初期住镇上，后期驻村）\n"
        "4. **调研节奏**：适应期→深入期→补充期的时间分配\n"
        "5. **缓冲设计**：预留应对天气、交通延误的机动时间\n"
        "6. **备选路线**：首选不可达时的替代方案\n\n"
        "使用可用的检索能力获取具体的航班、火车、住宿与兴趣点信息，"
        "整合成可执行的日程表。输出要详细、可执行，能支撑开题汇报或申报"
        "材料中的可行性论证；预算标注来源与完整性，缺报价的条目如实标记"
        "待核验而非硬凑。"
    ),
    "proposal_generation": (
        "你是田野罗盘 Agent。当前处于“方案生成”阶段。\n\n"
        "请将前面收集的所有信息整合成一份完整的 Research Proposal（研究方案），"
        "格式要求：\n\n"
        "# [研究标题]\n\n"
        "## 一、研究背景与问题提出\n"
        "## 二、田野点选择依据（含交通/住宿可行性分析）\n"
        "## 三、研究设计与方法论\n"
        "## 四、详细行程安排（含具体航班/车次/酒店信息）\n"
        "## 五、预算明细（基于实时搜索数据）\n"
        "## 六、风险评估与伦理预案\n"
        "## 七、预期成果\n"
        "## 八、参考文献\n\n"
        "确保语言学术化、正式，结构清晰，数据具体而非泛泛而谈。"
        "**引用红线**：只使用上下文中提供的资料条目，缺材料时在第八章列出"
        "“待补检索清单”，绝不虚构或编造引用。当前输出为通用八章体例，"
        "开题/基金体例尚未实现时要如实声明。"
    ),
    "finished": (
        "田野调研方案已完成。你可以：\n\n"
        "1. 对方案进行逐段修改\n"
        "2. 调整某个具体细节\n"
        "3. 导出为新的版本\n"
        "4. 开始一个新的调研项目\n\n"
        "保持专业的学术写作风格，随时准备根据反馈迭代方案。"
    ),
}

_PUBLIC_GENERATION_PROMPTS: dict[str, str] = {
    "suggest_system": (
        "你帮助田野调查新手规划访谈对象：围绕研究主题列出建议接触的人群类型，"
        "覆盖不同性别、年龄段与村里村外的视角，兼顾可触达性与基本伦理注意。"
    ),
    "guide_system": (
        "你为田野调查新手撰写访谈提纲：按通用访谈方法，"
        "为每类访谈对象起草可执行的访谈提纲。"
    ),
    "guide_instruction": (
        "请参考上述方法要点，为该类型访谈对象起草半结构式访谈提纲："
        "开场、主问、追问、敏感话题处理、知情同意说明与访后整理。"
    ),
}


# ------------------------------------------------------------------ Tier S 覆盖机制

_MISSING = object()
_LOCK = threading.Lock()
_FILE_CACHE: dict[str, Any] = {}
_OVERRIDE_DIR: Path | None = None  # reload() 显式指定时优先于探测链


def reload(assets_dir: Path | None = None) -> None:
    """显式指定覆盖目录并清空缓存；``None`` 恢复自动探测。"""
    global _OVERRIDE_DIR
    with _LOCK:
        _OVERRIDE_DIR = Path(assets_dir) if assets_dir is not None else None
        _FILE_CACHE.clear()


def _detect_dir() -> Path | None:
    env = os.environ.get(_ENV_VAR)
    if env:
        env_dir = Path(env)
        if not env_dir.is_dir():
            raise AssetsError(f"{_ENV_VAR} 指向的资产目录不存在: {env_dir}")
        return env_dir
    repo_secrets = _REPO_ROOT / "secrets"
    if repo_secrets.is_dir():
        return repo_secrets
    if _HOME_ASSETS_DIR.is_dir():
        return _HOME_ASSETS_DIR
    return None


def _resolve_dir() -> Path | None:
    if _OVERRIDE_DIR is not None:
        if not _OVERRIDE_DIR.is_dir():
            raise AssetsError(f"资产覆盖目录不存在: {_OVERRIDE_DIR}")
        return _OVERRIDE_DIR
    return _detect_dir()


def _load_override(asset_key: str) -> Any:
    """读覆盖包内某资产；无覆盖返回 ``_MISSING``（调用方回落公开版）。"""
    with _LOCK:
        if asset_key in _FILE_CACHE:
            return _FILE_CACHE[asset_key]
    directory = _resolve_dir()
    if directory is None:
        return _MISSING
    path = directory / _ASSET_FILES[asset_key]
    if not path.exists():
        return _MISSING
    try:
        if path.suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetsError(f"资产文件无法解析: {path} ({exc})") from exc
    with _LOCK:
        _FILE_CACHE[asset_key] = value
    return value


# ------------------------------------------------------------------ 访问器


def informant_library() -> dict[str, dict]:
    value = _load_override("informant_library")
    data = _PUBLIC_INFORMANT_LIBRARY if value is _MISSING else value
    if not isinstance(data, dict) or not data:
        raise AssetsError("informant_library.json 必须是非空对象（键=词条 type）")
    for key, entry in data.items():
        if not isinstance(entry, dict):
            raise AssetsError(f"词条 {key!r} 必须是对象")
    return copy.deepcopy(data)


def example_types() -> list[str]:
    value = _load_override("example_types")
    data = _PUBLIC_EXAMPLE_TYPES if value is _MISSING else value
    if (not isinstance(data, list) or not data
            or not all(isinstance(x, str) and x for x in data)):
        raise AssetsError("example_types.json 必须是非空字符串数组")
    library = informant_library()
    unknown = [k for k in data if k not in library]
    if unknown:
        raise AssetsError(f"example_types 含 informant_library 之外的键: {unknown}")
    return list(data)


def interview_methodology() -> str:
    value = _load_override("interview_methodology")
    data = _PUBLIC_METHODOLOGY if value is _MISSING else value
    if not isinstance(data, str) or not data.strip():
        raise AssetsError("interview_methodology.txt 必须是非空文本")
    return data


def system_prompts() -> dict[str, str]:
    value = _load_override("system_prompts")
    data = _PUBLIC_SYSTEM_PROMPTS if value is _MISSING else value
    if not isinstance(data, dict):
        raise AssetsError("system_prompts.json 必须是对象")
    missing = [k for k in _PHASE_KEYS if k not in data]
    if missing:
        raise AssetsError(f"system_prompts 覆盖包缺少阶段键: {missing}")
    for k in _PHASE_KEYS:
        if not isinstance(data[k], str) or not data[k].strip():
            raise AssetsError(f"system_prompts[{k}] 必须是非空字符串")
    return copy.deepcopy(data)


def generation_prompt(key: str) -> str:
    if key not in _GENERATION_KEYS:
        raise ValueError(f"未知 generation prompt 键: {key}")
    value = _load_override("generation_prompts")
    data = _PUBLIC_GENERATION_PROMPTS if value is _MISSING else value
    if not isinstance(data, dict):
        raise AssetsError("generation_prompts.json 必须是对象")
    if key not in data or not isinstance(data[key], str) or not data[key].strip():
        raise AssetsError(f"generation_prompts 覆盖包缺少有效键: {key}")
    return data[key]
```

- [ ] **Step 4: 跑 test_assets.py 确认全绿**

Run: `python -m pytest tests/test_assets.py -v`
Expected: 12 passed

- [ ] **Step 5: tests/conftest.py 末尾追加 autouse fixture（全文如下，直接接在现有内容后）**

```python


@pytest.fixture(autouse=True)
def _public_assets(tmp_path, monkeypatch):
    """全部测试默认运行在 Tier P 公开版资产下：指向空目录，禁用本地
    repo/secrets 与 ~/.fieldwork_agent/assets 覆盖，避免私有包泄入测试。"""
    empty = tmp_path / "_empty_assets"
    empty.mkdir()
    monkeypatch.delenv("FIELDWORK_ASSETS_DIR", raising=False)
    from fieldwork_agent.kernel import assets
    assets.reload(empty)
    yield
    assets.reload(empty)
```

- [ ] **Step 6: 跑全量测试确认无回归**

Run: `python -m pytest -q`
Expected: 全部通过（此时 informants/phases 仍用私有常量，fixture 无副作用）

- [ ] **Step 7: Commit**

```bash
git add kernel/assets.py tests/test_assets.py tests/conftest.py
git commit -m "feat: add tiered asset loader kernel/assets.py with public Tier P assets"
```

---

### Task 2: secrets 资产包导出与私有仓库初始化

**Files:**
- Create: `secrets/export_from_legacy.py`（不入主仓，位于 secrets/ 内）
- Create: `secrets/README.md`（不入主仓）
- Modify: `.gitignore`（追加 secrets/）

**Interfaces:**
- Consumes: Task 1 的 `fieldwork_agent.kernel.assets`（文件名约定五文件：informant_library.json / example_types.json / interview_methodology.txt / system_prompts.json / generation_prompts.json）；**当前仍在库里的私有常量** `informants.INFORMANT_LIBRARY`、`informants._EXAMPLE_TYPES`、`informants.INTERVIEW_GUIDE_METHODOLOGY`、`phases.SYSTEM_PROMPTS`
- Produces: `secrets/` 下五个资产文件 + `feature-strings.txt` + README，构成独立私有 git 仓库（Tier S 完整版）

**注意：本任务必须在 Task 3（删常量）之前完成；导出后跑一次全量测试验证资产包可用。**

- [ ] **Step 1: .gitignore 追加 secrets/**

在 `.gitignore` 的“运行期产物”区块之后（即 `# 编辑器 / 系统` 之前）插入：

```
# 技术秘密（Tier S 资产包，独立私有仓库管理，勿提交主仓）
secrets/
```

- [ ] **Step 2: Commit .gitignore**

```bash
git add .gitignore
git commit -m "chore: ignore secrets/ tier-S asset directory"
```

- [ ] **Step 3: 创建 secrets/README.md（全文）**

```markdown
# Tier S 私有资产包（技术秘密）

本目录是 Fieldwork Agent 的完整版 prompt/示范资产，独立私有 git 仓库管理，
绝不提交到开源主仓（主仓 `.gitignore` 已忽略 `secrets/`，CI 有泄漏 guard）。

## 文件结构

| 文件 | 内容 | 加载键 |
|---|---|---|
| informant_library.json | 完整报道人示范库（键=词条 type） | informant_library |
| example_types.json | 生效示范词条清单（字符串数组） | example_types |
| interview_methodology.txt | 完整版访谈方法论 | interview_methodology |
| system_prompts.json | 五阶段完整版系统提示词 | system_prompts |
| generation_prompts.json | suggest_system / guide_system / guide_instruction | generation_prompt(key) |
| feature-strings.txt | 私有特征串清单（M3 git filter-repo 演练用） | 不加载 |

## 生效方式

主仓 `kernel/assets.py` 按探测链读取本目录：仓库根存在 `secrets/` 即自动生效；
也可用环境变量 `FIELDWORK_ASSETS_DIR` 指向本目录或其副本。覆盖按资产键整体替换，
未提供的资产回落主仓内置公开版（Tier P）。

## 维护约定

- 修改资产直接改本目录文件；主仓公开版（kernel/assets.py 内 `_PUBLIC_*`）保持“只降不升”
- 新增词条/提示词时同步更新 feature-strings.txt（M3 git filter-repo blob 回调识别用）
- 本私有仓库的远程请使用私有托管，勿推送到公开平台

## 一次性迁移

`export_from_legacy.py` 从改造前的主仓常量导出本包（M0 使用一次，之后可删）。
```

- [ ] **Step 4: 创建 secrets/export_from_legacy.py（全文）**

```python
"""一次性迁移工具：从改造前的主仓常量导出 Tier S secrets 资产包。

M0 使用一次；之后主仓常量将被删除（Task 3+），本脚本随之失效可删。
运行：在主仓根目录 `python secrets/export_from_legacy.py`。
"""
import json
from pathlib import Path

from fieldwork_agent.kernel.guidance import informants, phases

OUT = Path(__file__).resolve().parent

# 生成型提示词原为函数内字面量，无法 import，在此硬编码原值。
GENERATION_PROMPTS = {
    "suggest_system": (
        "你是田野调查的信息提供者（报道人）设计助手：为研究主题铺设"
        "覆盖不同立场、年龄与权力位置的访谈对象谱系，兼顾可触达性与伦理风险。"
    ),
    "guide_system": (
        "你是田野访谈提纲撰写助手，按半结构式方法论为每类报道人起草针对性提纲。"
    ),
    "guide_instruction": (
        "请按上述方法论要点，为该类型报道人起草半结构式访谈提纲："
        "开场—主问—追问—敏感问题处理—知情同意话术（可照读版本）—访后动作。"
    ),
}

# 公开库（Tier P）保留的三词条，差集之外的都是私有特征。
FEATURE_LIBRARY_KEEP = {"村干部", "乡村教师", "返乡务工者"}

# 逐字验证过的私有特征串：每条必须命中导出内容（防拼写漂移）。
FIXED_FEATURE_STRINGS = [
    "半结构式访谈方法论要点",
    "像一位有经验的导师在指导研究生",
    "利用飞猪的旅行搜索能力",
    "嵌入图片链接和预订链接",
    "权力姿态会显著改变语料",
    "沉默往往意味着对方在组织真实叙述",
    "把学术概念留到事后转录时再翻译",
    "由浅入深三步",
    "材料安全与受访者处境优先于语料完整",
    "知情同意话术（可照读）",
    "这段别记",
    "**参考文献红线**",
    "覆盖不同立场、年龄与权力位置的访谈对象谱系",
    "知情同意话术（可照读版本）",
]


def main() -> None:
    library = informants.INFORMANT_LIBRARY
    methodology = informants.INTERVIEW_GUIDE_METHODOLOGY
    prompts = phases.SYSTEM_PROMPTS

    OUT.joinpath("informant_library.json").write_text(
        json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT.joinpath("example_types.json").write_text(
        json.dumps(list(informants._EXAMPLE_TYPES), ensure_ascii=False, indent=2),
        encoding="utf-8")
    OUT.joinpath("interview_methodology.txt").write_text(
        methodology, encoding="utf-8")
    OUT.joinpath("system_prompts.json").write_text(
        json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT.joinpath("generation_prompts.json").write_text(
        json.dumps(GENERATION_PROMPTS, ensure_ascii=False, indent=2),
        encoding="utf-8")

    # 自检：固定特征串必须在导出内容中出现（防拼写漂移）
    blobs = [
        json.dumps(library, ensure_ascii=False),
        methodology,
        json.dumps(prompts, ensure_ascii=False),
        json.dumps(GENERATION_PROMPTS, ensure_ascii=False),
    ]
    for s in FIXED_FEATURE_STRINGS:
        assert any(s in b for b in blobs), f"特征串未命中（检查拼写）: {s}"

    hidden_keys = sorted(set(library) - FEATURE_LIBRARY_KEEP)
    lines = ["# 私有资产特征串（git filter-repo blob 回调识别用）", "",
             "## 词条键（公开库之外的）"]
    lines += hidden_keys
    lines += ["", "## 私有提示词/方法论特征短语"]
    lines += FIXED_FEATURE_STRINGS
    OUT.joinpath("feature-strings.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    print(f"导出完成 -> {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 运行导出**

Run（主仓根目录）: `python secrets/export_from_legacy.py`
Expected: 输出 `导出完成 -> ...\secrets`；无 AssertionError

- [ ] **Step 6: 校验资产包可用**

```bash
python -c "import json,pathlib; d=pathlib.Path('secrets'); print(sorted(p.name for p in d.iterdir())); json.loads((d/'informant_library.json').read_text(encoding='utf-8')); json.loads((d/'system_prompts.json').read_text(encoding='utf-8')); json.loads((d/'generation_prompts.json').read_text(encoding='utf-8')); print('OK')"
```
Expected: 列出 7 个文件（五资产 + feature-strings.txt + export_from_legacy.py + README.md 中除脚本自身外均可见）且输出 `OK`

再跑一次全量：`python -m pytest -q` —— 此时 secrets/ 已存在且自动生效（Tier S 完整版接管），全部测试仍应通过（验证“完整版替换公开版”路径）。

- [ ] **Step 7: 初始化私有 git 仓库**

```bash
git -C secrets init
git -C secrets add -A
git -C secrets commit -m "feat: initial Tier S private asset package (exported from legacy constants)"
```

- [ ] **Step 8: 确认主仓不跟踪 secrets/**

Run: `git status --short`
Expected: 无任何 `secrets/` 条目（被 .gitignore 忽略）；工作目录干净

---

### Task 3: kernel/guidance/informants.py 重构（删常量 → assets 动态）

**Files:**
- Modify: `kernel/guidance/informants.py`
- Test: `tests/test_guidance.py`（7 处断言改公开版）

**Interfaces:**
- Consumes: Task 1 的 `assets.informant_library() / example_types() / interview_methodology()`
- Produces: `informants` 模块不变的面——`INFORMANT_LIBRARY` / `INFORMANT_EXAMPLES` / `INTERVIEW_GUIDE_METHODOLOGY`（改为 PEP 562 动态属性）、`get_informant(type) -> dict | None`、`match_examples(topic) -> list[dict]`、`_to_archive(key, entry)`、`MAX_MATCHES=6`。后续任务（documents/resources/测试）全部依赖这些名字不变。

- [ ] **Step 1: 改 test_guidance.py 断言（公开版口径）**

逐处修改（行号基于当前文件）：

| 行 | 旧 | 新 |
|---|---|---|
| L39 | `assert len(informants.INFORMANT_LIBRARY) >= 10` | `assert len(informants.INFORMANT_LIBRARY) >= 3` |
| L40 | `assert "长老" in informants.INFORMANT_LIBRARY` | `assert "村干部" in informants.INFORMANT_LIBRARY` |
| L215 | `assert informants.get_informant("长老") is not None` | `assert informants.get_informant("村干部") is not None` |
| L237 | `one = informants.get_informant("长老")` | `one = informants.get_informant("村干部")` |
| L243 | `assert len(one["examples"]) >= 5` | `assert len(one["examples"]) >= 3` |
| L244 | 该行内的 `"长老"` 字面量 | `"村干部"` |
| L251 | `assert 5 <= len(informants.INFORMANT_EXAMPLES) <= 6` | `assert len(informants.INFORMANT_EXAMPLES) == 3` |

**不改**：L28/L126 的 `InformantRecord(type="长老", ...)` 是用户输入数据，与库无关；L44-46（phase_prompt 无 `{`）、L64-65（“学科取向”/“时间窗口”）、L163-175、L204-224（八字段/方法论五 token）、L262-280（match_examples 字段与兕底）全部保持——公开版资产已满足这些断言。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_guidance.py -v`
Expected: `test_informant_examples_*`（L251 处）FAIL —— 私有版 `INFORMANT_EXAMPLES` 长度为 6，期望 3；其余“长老”→“村干部”处仍过（私有库含村干部）

- [ ] **Step 3: 重构 informants.py**

1. 顶部 import 区加：`from fieldwork_agent.kernel import assets`
2. **删除四个数据常量定义**（保留其余一切）：`INFORMANT_LIBRARY = {...}`（大字典，含“长老”等 10 词条）、`INTERVIEW_GUIDE_METHODOLOGY = ...`（“# 半结构式访谈方法论要点”全文）、`_EXAMPLE_TYPES = (...)`、`INFORMANT_EXAMPLES = {...}`（含 L492-494 推导式）
3. 在原 `INFORMANT_EXAMPLES` 位置加入以下代码（全文）：

```python
def _current_examples() -> dict[str, dict]:
    """按生效资产构建 {type: archive} 示范档案（每次调用重建）。"""
    library = assets.informant_library()
    return {key: _to_archive(key, library[key]) for key in assets.example_types()}


def __getattr__(name: str):
    """PEP 562：常量属性动态代理到 assets，保持旧 import 兼容。

    注意 ``from ... informants import INFORMANT_LIBRARY`` 得到的是当次快照，
    不随后续 reload() 更新——需要动态语义时用属性访问或直接调 assets。
    """
    if name == "INFORMANT_LIBRARY":
        return assets.informant_library()
    if name == "INFORMANT_EXAMPLES":
        return _current_examples()
    if name == "INTERVIEW_GUIDE_METHODOLOGY":
        return assets.interview_methodology()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

4. `get_informant` 改为（全文）：

```python
def get_informant(type: str) -> dict | None:
    entry = assets.informant_library().get(type)
    return copy.deepcopy(entry) if entry is not None else None
```

5. `match_examples` 函数体开头加 `examples = _current_examples()`，函数内三处 `INFORMANT_EXAMPLES` 引用改为 `examples`（含空主题全返、enumerate 迭代、兕底 `list(examples.values())`）。匹配算法本身（_bigrams/排序/前 MAX_MATCHES）不动。
6. 保留不动：`_to_archive`、`MAX_MATCHES`、`_PUNCT_RE`、`_bigrams`。

- [ ] **Step 4: 跑 test_guidance.py 确认全绿**

Run: `python -m pytest tests/test_guidance.py -v`
Expected: 全部 PASS（conftest fixture 已把资产锁定在公开版）

- [ ] **Step 5: 跑全量确认无回归**

Run: `python -m pytest -q`
Expected: 全部通过。若 test_documents / test_mcp_resources_prompts 在本步报“长老”相关失败属正常——它们在 Task 5/6 修复；此时先记录，不提交半成品，继续按任务顺序执行（本步若只 guidance/documents/mcp 中 guidance 已绿即可提交本任务，documents/mcp 失败项留给对应任务）。

- [ ] **Step 6: Commit**

```bash
git add kernel/guidance/informants.py tests/test_guidance.py
git commit -m "refactor: informants constants proxied to tiered assets via PEP 562"
```

---

### Task 4: kernel/guidance/phases.py 改造（删 SYSTEM_PROMPTS → assets 动态）

**Files:**
- Modify: `kernel/guidance/phases.py`（删 L41-111 的 `SYSTEM_PROMPTS`；改 `phase_prompt`）
- Test: 无新增——`tests/test_guidance.py` 现有断言即验收（公开版提示词含“学科取向”“时间窗口”“虚构”等 token，无 `{`）

**Interfaces:**
- Consumes: Task 1 的 `assets.system_prompts()`（五阶段键齐全）
- Produces: `phases.SYSTEM_PROMPTS`（动态属性，dict[str, str] 五键）；`phase_prompt(phase) -> str` 行为不变（未知阶段回落 research_positioning）

- [ ] **Step 1: 重构 phases.py**

1. 顶部 import 区加：`from fieldwork_agent.kernel import assets`
2. 删除 `SYSTEM_PROMPTS: dict[str, str] = {...}` 整块（L41-111，私有五阶段提示词全文）
3. 原位置加入（全文）：

```python
def __getattr__(name: str):
    """PEP 562：SYSTEM_PROMPTS 动态代理到 assets（旧 import 得到快照）。"""
    if name == "SYSTEM_PROMPTS":
        return assets.system_prompts()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

4. `phase_prompt` 改为（全文）：

```python
def phase_prompt(phase: str) -> str:
    prompts = assets.system_prompts()
    return prompts.get(phase, prompts[PHASE_RESEARCH_POSITIONING])
```

5. 保留不动：五个阶段键常量、`GUIDING_QUESTIONS`、`REQUIRED_FIELDS`、`check_advance`（Tier O）。

- [ ] **Step 2: 跑 guidance 测试确认全绿**

Run: `python -m pytest tests/test_guidance.py -v`
Expected: 全部 PASS（L44-46 无 `{`、L64-65 学科取向/时间窗口、L171-175 虚构/无基金委，公开版提示词均满足）

- [ ] **Step 3: 跑全量确认无回归**

Run: `python -m pytest -q`
Expected: 除 test_documents / test_mcp_resources_prompts 的已知“长老”/私有特征串失败（Task 5/6 修）外全部通过

- [ ] **Step 4: Commit**

```bash
git add kernel/guidance/phases.py
git commit -m "refactor: phases.SYSTEM_PROMPTS proxied to tiered assets"
```

---

### Task 5: kernel/documents 两文件改走 assets.generation_prompt

**Files:**
- Modify: `kernel/documents/informants.py`
- Modify: `kernel/documents/proposal.py`
- Test: `tests/test_documents.py`（4 处断言改公开版）

**Interfaces:**
- Consumes: Task 1 的 `assets.generation_prompt("suggest_system" / "guide_system" / "guide_instruction")`；Task 3 的 `informants.INTERVIEW_GUIDE_METHODOLOGY / get_informant`（动态属性，无需改）
- Produces: `suggest_informants` / `compose_interview_guide` 签名与消息结构不变（system+user 消息顺序、slot/max_tokens/temperature 不变）

- [ ] **Step 1: 改 test_documents.py 断言（公开版口径）**

| 位置 | 旧 | 新 | 原因 |
|---|---|---|---|
| L170（unmatched 测试） | `assert "半结构式访谈方法论要点" in llm.messages[-1]["content"]` | `assert "访谈方法要点（开源简版）" in llm.messages[-1]["content"]` | 公开版方法论标题 |
| L174（degrade 测试） | `["长老"]` | `["村干部"]` | 公开库词条 |
| L175 | `{"长老"}` | `{"村干部"}` | 同上 |
| L177 | `guides["长老"]` | `guides["村干部"]` | 同上 |
| L178 | `assert "提问域" in guides["长老"]` | `assert "提问域" in guides["村干部"]` | 村干部在库，走 entry 分支才有 `## 提问域` |

**不改**：L52-60 per_type 测试中的“长老”“集市商户”（FakeLLM 返回值断言，与库无关）；模块内 FakeLLM 定义（L20-29）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_documents.py -v`
Expected: L170/L173-178 所在两个测试 FAIL（当前仍是私有提示词/“长老”在库走 entry 分支）

- [ ] **Step 3: 改 documents/informants.py**

1. 在 `from fieldwork_agent.kernel.guidance import informants as informant_lib`（L16）之后加一行：`from fieldwork_agent.kernel import assets`
2. suggest 函数里的 system 消息（原为私有字面量“你是田野调查的信息提供者（报道人）设计助手：…谱系，兼顾可触达性与伦理风险。”）改为：

```python
        {"role": "system", "content": assets.generation_prompt("suggest_system")},
```

3. 其余（user prompt 输出契约段、`llm.chat(messages, slot="proposal", max_tokens=4096, temperature=0.5)`）不动。

- [ ] **Step 4: 改 documents/proposal.py**

1. 在 `from fieldwork_agent.kernel.guidance import informants, phases`（L16）之后加一行：`from fieldwork_agent.kernel import assets`
2. `_guide_prompt` 中末尾两行字符串字面量（“请按上述方法论要点，为该类型报道人起草半结构式访谈提纲：”“开场—主问—追问—敏感问题处理—知情同意话术（可照读版本）—访后动作。”）合并替换为：

```python
           assets.generation_prompt("guide_instruction")]
```

（即 parts 列表末项由私有指令串改为动态指令；前面的 `f"{informants.INTERVIEW_GUIDE_METHODOLOGY}\n\n"` 保持属性访问不动。）

3. `compose_interview_guide` 中 system 消息（原私有句“你是田野访谈提纲撰写助手，按半结构式方法论为每类报道人起草针对性提纲。”）改为：

```python
        {"role": "system", "content": assets.generation_prompt("guide_system")},
```

4. `_guide_degrade` 与 `_entry_block` 不动：`informants.get_informant(itype)` / `informants.INTERVIEW_GUIDE_METHODOLOGY` 均为属性访问，Task 3 后自动动态。

- [ ] **Step 5: 跑 test_documents.py 确认全绿**

Run: `python -m pytest tests/test_documents.py -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add kernel/documents/informants.py kernel/documents/proposal.py tests/test_documents.py
git commit -m "refactor: document generation prompts sourced from tiered assets"
```

---

### Task 6: mcp_server/resources.py 改属性访问（动态生效）

**Files:**
- Modify: `mcp_server/resources.py`
- Test: `tests/test_mcp_resources_prompts.py`（L23 一处）

**Interfaces:**
- Consumes: Task 3 的 `informants.INFORMANT_EXAMPLES / get_informant / INTERVIEW_GUIDE_METHODOLOGY`（模块属性访问）
- Produces: `STATIC_RESOURCES`（import 时快照，不变行为）；`read_resource` / `list_resources` 动态展开当前生效资产

**背景**：`from ... import INFORMANT_EXAMPLES` 得到的是 import 时快照，`reload()` 后不更新；改模块属性访问后，`read_resource` 与 `list_resources` 在每次调用时动态取值。

- [ ] **Step 1: 改 test_mcp_resources_prompts.py L23**

旧：`assert "fieldwork://guidance/informants/长老" in uris`
新：`assert "fieldwork://guidance/informants/村干部" in uris`

（L47 “参考起点”、L56 “田野点画像”断言不动——公开版资源文案与 B 提示词已满足。）

- [ ] **Step 2: 重构 resources.py**

1. L14-18 的多行 from-import：

```python
from fieldwork_agent.kernel.guidance.informants import (
    INFORMANT_EXAMPLES, INTERVIEW_GUIDE_METHODOLOGY, get_informant,)
```

改为：

```python
from fieldwork_agent.kernel.guidance import informants as informant_lib
```

2. 四处引用同步改名（均为 `informant_lib.` 前缀）：
   - L73 `for key, archive in informant_lib.INFORMANT_EXAMPLES.items():`
   - L92 `entry = informant_lib.get_informant(informant_type)`
   - L114 `return informant_lib.INTERVIEW_GUIDE_METHODOLOGY`
   - L199 `for key in informant_lib.INFORMANT_EXAMPLES:`
3. L70 固定文案“> 以下为参考起点，非枚举白名单”与其余逻辑不动。

- [ ] **Step 3: 跑测试确认全绿**

Run: `python -m pytest tests/test_mcp_resources_prompts.py tests/test_mcp_server_contract.py -v`
Expected: 全部 PASS

- [ ] **Step 4: 跑全量确认（此时应已无遗留失败）**

Run: `python -m pytest -q`
Expected: 全部通过（Task 3/4/5 遗留的“长老”失败已全部修复）

- [ ] **Step 5: Commit**

```bash
git add mcp_server/resources.py tests/test_mcp_resources_prompts.py
git commit -m "refactor: mcp resources read informants via module attribute access"
```

---

### Task 7: 覆盖端到端测试 tests/test_assets_override.py

**Files:**
- Test: `tests/test_assets_override.py`（新建，覆盖面验收）

**Interfaces:**
- Consumes: Task 1 的 `assets.reload`；Task 3 的 `informants.*` 动态属性；Task 4 的 `phases.SYSTEM_PROMPTS / phase_prompt`；Task 5 的 `suggest_informants(research_topic, discipline="", site_profile=None, *, llm=None) -> Envelope`、`compose_interview_guide(topic, informant_types, *, llm=None) -> dict[str, str]`；Task 6 的 `resources.STATIC_RESOURCES / read_resource(repo, uri)`
- Produces: 无（纯测试，验证分级机制端到端正确）

- [ ] **Step 1: 写 `tests/test_assets_override.py`（全文）**

```python
"""覆盖端到端：Tier S 假包切换后，guidance/documents/mcp 全链路动态生效。"""
import json

import pytest

from fieldwork_agent.kernel import assets
from fieldwork_agent.kernel.guidance import informants, phases
from fieldwork_agent.kernel.documents.informants import suggest_informants
from fieldwork_agent.kernel.documents.proposal import compose_interview_guide
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server import resources

PHASE_KEYS = [
    "research_positioning", "field_site_profiles", "itinerary_planning",
    "proposal_generation", "finished",
]
FAKE_LIBRARY = {
    "测试甲": {"type": "测试甲", "approach": "甲式接触", "cautions": "甲注意",
             "question_domains": ["域一"], "ethics": "甲伦理",
             "access_channel": "甲渠道", "preparation": "甲准备",
             "examples": ["问一", "问二", "追问：三"]},
    "测试乙": {"type": "测试乙", "approach": "乙式接触", "cautions": "乙注意",
             "question_domains": ["域二"], "ethics": "乙伦理",
             "access_channel": "乙渠道", "preparation": "乙准备",
             "examples": ["问一", "问二", "追问：三"]},
}
FAKE_PROMPTS = {k: f"假-{k}" for k in PHASE_KEYS}
FAKE_GENERATION = {
    "suggest_system": "假生成-suggest_system",
    "guide_system": "假生成-guide_system",
    "guide_instruction": "假生成-guide_instruction",
}


class FakeLLM:
    def __init__(self, out):
        self.out = out
        self.messages = []

    def chat(self, messages, **kwargs):
        self.messages.append(list(messages))
        return self.out


@pytest.fixture(autouse=True)
def _restore_assets(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assets.reload(empty)
    yield
    assets.reload(empty)


@pytest.fixture
def fake_pkg(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "informant_library.json").write_text(
        json.dumps(FAKE_LIBRARY, ensure_ascii=False), encoding="utf-8")
    (pkg / "example_types.json").write_text(
        json.dumps(["测试甲"], ensure_ascii=False), encoding="utf-8")
    (pkg / "interview_methodology.txt").write_text(
        "# 端到端假方法论", encoding="utf-8")
    (pkg / "system_prompts.json").write_text(
        json.dumps(FAKE_PROMPTS, ensure_ascii=False), encoding="utf-8")
    (pkg / "generation_prompts.json").write_text(
        json.dumps(FAKE_GENERATION, ensure_ascii=False), encoding="utf-8")
    return pkg


def test_informants_module_switches(fake_pkg):
    assets.reload(fake_pkg)
    assert set(informants.INFORMANT_LIBRARY) == {"测试甲", "测试乙"}
    assert set(informants.INFORMANT_EXAMPLES) == {"测试甲"}
    assert informants.get_informant("测试甲")["approach"] == "甲式接触"
    assert informants.get_informant("长老") is None
    assert informants.INTERVIEW_GUIDE_METHODOLOGY == "# 端到端假方法论"


def test_phases_module_switches(fake_pkg):
    assets.reload(fake_pkg)
    assert phases.SYSTEM_PROMPTS["itinerary_planning"] == "假-itinerary_planning"
    assert phases.phase_prompt("finished") == "假-finished"
    assert phases.phase_prompt("unknown-phase") == "假-research_positioning"


def test_match_examples_fallback_on_fake_pkg(fake_pkg):
    assets.reload(fake_pkg)
    out = informants.match_examples("完全无法匹配的主题 qwerty")
    # 全落空兕底：返回全部生效示范（假包仅“测试甲”）
    assert [o["type"] for o in out] == ["测试甲"]


def test_suggest_informants_uses_pkg_system(fake_pkg):
    assets.reload(fake_pkg)
    llm = FakeLLM({"content": "[]"})
    suggest_informants("主题", llm=llm)
    assert llm.messages[0]["role"] == "system"
    assert llm.messages[0]["content"] == "假生成-suggest_system"


def test_compose_interview_guide_uses_pkg(fake_pkg):
    assets.reload(fake_pkg)
    llm = FakeLLM({"content": "提纲正文"})
    guides = compose_interview_guide("主题", ["测试甲"], llm=llm)
    assert llm.messages[0]["role"] == "system"
    assert llm.messages[0]["content"] == "假生成-guide_system"
    user = llm.messages[1]["content"]
    assert "# 端到端假方法论" in user
    assert "假生成-guide_instruction" in user
    assert guides["测试甲"] == "提纲正文"


def test_resources_dynamic_vs_snapshot(fake_pkg, tmp_path):
    before = [r.uri for r in resources.STATIC_RESOURCES]
    assets.reload(fake_pkg)
    # STATIC_RESOURCES 是 import 时快照，不随 reload 变
    assert [r.uri for r in resources.STATIC_RESOURCES] == before
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    out = resources.read_resource(repo, "fieldwork://guidance/informants/library")
    assert out["ok"] and "测试甲" in out["text"]
    method = resources.read_resource(
        repo, "fieldwork://guidance/interview-guide/methodology")
    assert method["ok"] and "# 端到端假方法论" in method["text"]
```

- [ ] **Step 2: 跑测试确认全绿（覆盖机制已就绪，应直接通过）**

Run: `python -m pytest tests/test_assets_override.py -v`
Expected: 6 passed。若有失败，说明 Task 1-6 某处动态链路未接通——按失败点回查对应任务，不得在本任务用 hack 绕过。

- [ ] **Step 3: 跑全量确认无回归**

Run: `python -m pytest -q`
Expected: 全部通过

- [ ] **Step 4: Commit**

```bash
git add tests/test_assets_override.py
git commit -m "test: end-to-end tier-S override across guidance/documents/mcp"
```

---

### Task 8: CI secrets 泄漏 guard

**Files:**
- Create: `tools/check_secrets_leak.py`
- Modify: `.github/workflows/ci.yml`（pytest 步骤后追加一步）

**Interfaces:**
- Consumes: git CLI（`git ls-files` / `git grep --fixed-strings`）
- Produces: 退出码 0（干净）/ 1（泄漏，stdout 列出问题）；CI 步骤 `Secrets-leak guard`

- [ ] **Step 1: 写 `tools/check_secrets_leak.py`（全文）**

```python
"""CI guard：主仓不得跟踪 secrets/，不得含私有资产特征串。

退出码 0 = 干净；1 = 发现泄漏（stdout 列出问题）。完整特征串清单在
secrets/feature-strings.txt 维护，这里只放三条最强信号（措辞刻意、不易误伤）。
"""
import subprocess
import sys

MINIMAL_FEATURE_STRINGS = [
    "半结构式访谈方法论要点",
    "像一位有经验的导师在指导研究生",
    "覆盖不同立场、年龄与权力位置的访谈对象谱系",
]


def run_git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False).stdout


def main() -> int:
    problems = []
    tracked = [l for l in run_git("ls-files").splitlines()
               if l.startswith("secrets/")]
    if tracked:
        problems.append(f"secrets/ 下有被跟踪文件: {tracked}")
    for s in MINIMAL_FEATURE_STRINGS:
        # git grep 无匹配时 exit 1、stdout 空——只看 stdout 非空即命中。
        # pathspec 排除 docs/（计划与 spec 文档含特征串明文，其清洗由 M3 历史
        # 重写兜底）与本脚本自身（特征串字面量所在）——否则 guard 永远自命中。
        hits = run_git("grep", "-l", "--fixed-strings", s, "--",
                       ":(exclude)docs", ":(exclude)tools/check_secrets_leak.py").strip()
        if hits:
            problems.append(f"特征串 {s!r} 命中被跟踪文件:\n{hits}")
    if problems:
        print("SECRETS LEAK DETECTED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("secrets-leak guard: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 本地验证 guard（此时主仓已无私有常量，应干净）**

Run: `python tools/check_secrets_leak.py`
Expected: 输出 `secrets-leak guard: clean`，退出码 0

- [ ] **Step 3: ci.yml 在 pytest 步骤之后追加 guard 步骤**

在 `.github/workflows/ci.yml` 中 `- run: python -m pytest -ra`（现有 pytest 步骤）之后插入：

```yaml
      - name: Secrets-leak guard
        run: python tools/check_secrets_leak.py
```

- [ ] **Step 4: 跑全量 + guard 联合确认**

Run: `python -m pytest -q; python tools/check_secrets_leak.py`
Expected: 测试全绿；guard clean

- [ ] **Step 5: Commit**

```bash
git add tools/check_secrets_leak.py .github/workflows/ci.yml
git commit -m "ci: add secrets-leak guard"
```

### Task 9: DEVELOPMENT.md 增补 + M0 验收

**Files:**
- Modify: `DEVELOPMENT.md`（文件末尾追加新章节）

**Interfaces:**
- Consumes: Task 1 的 `kernel/assets.py`（探测链/`AssetsError` 表述）；Task 2 的 `secrets/` 文件结构与 `feature-strings.txt`；Task 8 的 `tools/check_secrets_leak.py`
- Produces: 无（最终任务）。出口 = spec §12 的 M0 验收五条全部通过

- [ ] **Step 1: `DEVELOPMENT.md` 文末追加章节（全文如下，接在现有最后一章之后）**

```markdown
## 10. 资产分级与 secrets 维护

本项目的 prompt 体系与访谈示范档案库分三级管理，开源主仓只含公开层：

| 级别 | 内容 | 存放位置 | 加载方式 |
|---|---|---|---|
| **Tier S（私有）** | 完整版示范库（10 类词条）、完整访谈方法论、五阶段完整 system prompt、生成类完整提示词 | `secrets/`（独立私有 git 仓库，被主仓 `.gitignore` 忽略） | 运行时覆盖 Tier P |
| **Tier P（公开）** | 精简版：3 词条示范库、简版方法论、五阶段简版 prompt、简版 generation 措辞 | `kernel/assets.py` 内置常量 | 默认 |
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
```

- [ ] **Step 2: M0 验收（逐条覆盖 spec §12 五条；全部在主仓根目录执行）**

验收 1 —— 全量测试绿（公开版模式。此时本地已存在 `secrets/`，conftest 的 autouse fixture 会把测试钉在公开版——这同时验证了“本地开发者放有 secrets/ 跑测试不炸”）：

Run: `python -m pytest -q`
Expected: 全绿，0 failed

验收 2 —— env 指向真 secrets 包时完整版生效（spec §12 第 2 条；PowerShell）：

```powershell
$env:FIELDWORK_ASSETS_DIR = "$PWD/secrets"
python -c "from fieldwork_agent.kernel import assets; lib = assets.informant_library(); assert len(lib) == 10, len(lib); m = assets.interview_methodology(); assert '半结构式' in m; print('Tier S 生效:', len(lib), '类 + 完整方法论')"
Remove-Item Env:FIELDWORK_ASSETS_DIR
```

Expected: 输出 `Tier S 生效: 10 类 + 完整方法论`；随后 `Remove-Item` 清掉 env（不清会导致后续命令命中完整版）

（spec §12 第 2 条另含“行为评估集冒烟”——评估集为 spec 前瞻约定、尚未建立，M0 以本条手动检查代之；建立后纳入 secrets 变更回归流程，见 DEVELOPMENT.md 维护约定。）

验收 3 —— 主仓干净 + secrets/ 未跟踪 + guard clean（spec §12 第 3 条）：

Run: `git status --short` → Expected: 空输出（Task 9 Step 3 commit 之前会有 DEVELOPMENT.md 一条，commit 后复查为空）
Run: `git ls-files secrets/` → Expected: 空输出
Run: `python tools/check_secrets_leak.py` → Expected: `secrets-leak guard: clean`

验收 4 —— secrets 私有仓库已建立（spec §12 第 4 条）：

Run: `git -C secrets log --oneline`
Expected: 至少一行提交（Task 2 的首个 commit）
说明：spec 要求“已推送首个 commit”——远程推送为维护者手动步骤（自建私有远程后 `git -C secrets remote add origin <私有URL>`、`git -C secrets push -u origin master`），本地验收只确认私有仓存在提交，远程操作记录在 DEVELOPMENT.md 维护约定第 4 条。

验收 5 —— DEVELOPMENT.md 含资产分级维护章节（spec §12 第 5 条）：

Run: `Select-String -Path DEVELOPMENT.md -Pattern "资产分级与 secrets 维护"`
Expected: 命中一行（Step 1 写入的 `## 10.` 标题）

- [ ] **Step 3: Commit**

```bash
git add DEVELOPMENT.md
git commit -m "docs: asset tiering and secrets maintenance guide"
```

---

## Spec 覆盖对照（spec §13 M0 十项 → 本计划任务）

| Spec §13 条目 | 实现位置 |
|---|---|
| 1. `kernel/assets.py` + `tests/test_assets.py`（TDD 先测后实现） | Task 1 |
| 2. Tier P 公开版资产（3 词条、简版方法论、5 阶段简版提示词、公开版 generation 措辞；措辞差异化） | Task 1（`_PUBLIC_*` 常量内嵌于 `kernel/assets.py`） |
| 3. 重构 `kernel/guidance/informants.py` 接 assets | Task 3 |
| 4. 改造 `kernel/guidance/phases.py` | Task 4 |
| 5. 改造 `kernel/documents/informants.py`、`kernel/documents/proposal.py` | Task 5 |
| 6. 改造 `mcp_server/resources.py` | Task 6 |
| 7. 生成 secrets 资产包（五文件 + feature-strings.txt + README）并初始化私有仓库 | Task 2 |
| 8. `.gitignore` + CI secrets-leak guard | Task 2（.gitignore）+ Task 8（guard + CI 步骤） |
| 9. 测试全量改造与回归 | Task 1（conftest fixture）+ Task 3/5/6（各文件断言改公开版）+ Task 7（端到端覆盖测试 `test_assets_override.py`） |
| 10. `DEVELOPMENT.md` 增补章节；文档与本 spec 归档 | Task 9（章节 + M0 验收）；spec 已提交（93af96b），本计划文档随验收一并入库 |

M0 完成后的状态：主仓在任何无 `secrets/` 的环境（含 CI）下测试全绿、功能可用（公开版资产）；本地开发把 `secrets/` 放在 repo 根目录或设 `FIELDWORK_ASSETS_DIR` 即自动获得完整版资产；主仓历史与工作区均无私有特征串。
