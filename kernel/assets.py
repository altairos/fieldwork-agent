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

# informant_library 每个词条的必备字段（type 为兼容公开版的附加键，不作硬要求）。
_ENTRY_REQUIRED_FIELDS = (
    "approach", "cautions", "question_domains", "ethics",
    "access_channel", "preparation", "examples",
)
_ENTRY_LIST_FIELDS = ("question_domains", "examples")


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
        "2. **研究主题**：初步的研究方向或关注焦点，越具体越好。\n"
        "3. **深挖研究问题**：拿到初步方向后不要急着过下一项——先追问一轮："
        "ta 为什么关心这个问题（动机与现实关切）？希望具体回答哪 2-3 个"
        "研究问题？是否已有初步设想？与研究者一起把方向凝练成 2-3 个可研究"
        "的问题，经确认后用 update_research_context 写入 research_questions "
        "字段。\n"
        "4. **田野点确定程度**：已明确地点 / 有区域范围 / 完全开放由你推荐？\n"
        "5. **调研规模**：单人还是团队？预计调研时长（短期 2-4 周、中期 1-3 月、"
        "长期 3 月以上）？\n"
        "6. **经费来源与额度**：是否有经费支持？大致预算范围？\n"
        "7. **时间窗口**：希望在什么时间段进行调研？是否有硬性截止日期？\n\n"
        "推进节奏：以上七项（含深挖研究问题）确认齐备前，不进入下一阶段；"
        "研究问题没有深挖成型前尤其不要推进。\n\n"
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
        "“待补检索清单”，绝不虚构或编造引用。当前输出为英美式八章体例，"
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
    filename = _ASSET_FILES["informant_library"]
    if not isinstance(data, dict) or not data:
        raise AssetsError(f"{filename} 必须是非空对象（键=词条 type）")
    for key, entry in data.items():
        if not isinstance(entry, dict):
            raise AssetsError(f"词条 {key!r} 必须是对象（文件: {filename}）")
        missing = [f for f in _ENTRY_REQUIRED_FIELDS if f not in entry]
        if missing:
            raise AssetsError(
                f"词条 {key!r} 缺字段 {missing}（文件: {filename}）")
        for field in _ENTRY_LIST_FIELDS:
            if not isinstance(entry[field], list):
                raise AssetsError(
                    f"词条 {key!r} 字段 {field!r} 必须是列表（文件: {filename}）")
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
