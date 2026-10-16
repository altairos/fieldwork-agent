"""访谈对象（报道人）类型库 — 面向田野调查场景的结构化数据。

设计规格 §2.1：本模块是内核化的**学术纵深新增**（非迁移），把原先只存在于
对话习惯中的“该找谁问什么”显式化为可检索数据，供引导与访谈提纲生成消费。

约定：
- ``INFORMANT_LIBRARY`` 的键即 ``InformantRecord.type`` 可登记的类型名；
- 公开版（Tier P）每条固定七个内容字段：``approach``（接近策略）、
  ``cautions``（注意事项）、``question_domains``（建议提问域）、``ethics``（伦理提示）、
  ``access_channel``（进入田野途径）、``preparation``（行前保障）、
  ``examples``（2-3 条提问示例，末条为“追问：”句式）；secrets 覆盖版（Tier S）
  词条另含 ``name``（展示名）等扩充字段，消费方取展示名须以 ``get("name", <type>)`` 兜底；
- ``INFORMANT_EXAMPLES`` 是从库中精选的**示范词条**（公开版 3 条，secrets 覆盖版更多；各含 type + 六个内容字段
  approach/cautions/question_domains/ethics/access_channel/preparation，并附 examples），
  定位是 ``InformantCandidate`` 的写法示范（few-shot 质量锚）与无 key 降级兜底，
  **非枚举白名单**——候选的动态生成见 ``kernel/documents/informants.suggest_informants``；
- 纯数据，无 I/O；``get_informant`` 返回深拷贝，宿主改动不会污染库本身。
"""

import copy
import re

from fieldwork_agent.kernel import assets

# ---------------------------------------------------------------- 精选示范词条与主题匹配


def get_informant(type: str) -> dict | None:
    """按类型名取条目（与 ``InformantRecord.type`` 同键）；未收录返回 ``None``。"""
    entry = assets.informant_library().get(type)
    return copy.deepcopy(entry) if entry is not None else None


def _to_archive(key: str, entry: dict) -> dict:
    """LIBRARY 条目 → ``InformantCandidate`` 可 validate 的档案 dict（含附带 examples）。"""
    return {
        "type": key,
        "approach": entry["approach"],
        "cautions": entry["cautions"],
        "question_domains": list(entry["question_domains"]),
        "ethics": entry["ethics"],
        "access_channel": entry["access_channel"],
        "preparation": entry["preparation"],
        "examples": list(entry["examples"]),
    }


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


# 非空主题时的返回上限（``suggest_informants`` 的 1-6 类契约由此保底）
MAX_MATCHES = 6

# 二字组打分前剔除的标点/空白（中文无词边界，退化到字符二字组重叠做粗匹配）
_PUNCT_RE = re.compile(r"[\s/、，。；：·（）()《》\-]+")


def _bigrams(text: str) -> set[str]:
    cleaned = _PUNCT_RE.sub("", text)
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def match_examples(topic: str) -> list[dict]:
    """按主题关键词粗匹配精选示范词条，返回 ``InformantCandidate`` 字段可 validate 的档案 dict。

    匹配策略为字符二字组重叠计分（中文无词边界的确定性纯函数）：
    - 空主题 → 返回全部示范词条（``list(INFORMANT_EXAMPLES.values())``）；
    - 非空主题 → 取分数 >0 的前 ``MAX_MATCHES`` 条，按分数降序、同分保持精选序；
    - 全部落空 → 兜底返回全部：无 key 降级路径宁可给“参考起点”也不给空结果。
    """
    examples = _current_examples()
    topic = (topic or "").strip()
    if not topic:
        return list(examples.values())
    topic_bg = _bigrams(topic)
    scored: list[tuple[int, int, str]] = []
    for order, (key, archive) in enumerate(examples.items()):
        haystack = "".join([key, archive["approach"],
                            *archive["question_domains"], *archive["examples"]])
        score = len(topic_bg & _bigrams(haystack))
        if score > 0:
            scored.append((-score, order, key))
    if not scored:
        return list(examples.values())
    scored.sort()
    return [examples[k] for _, _, k in scored[:MAX_MATCHES]]
