"""消息信息提取纯函数 — 迁移自 ``core/dialogue_engine.py`` 的两个提取方法。

城市/省份词表与既有增量槽位沿用旧实现；学术背景另走保守规则与专名地点保护：
- 去掉 ``self`` 与 ``ProjectState`` 依赖，改为模块级纯函数；
- 原实现中反复出现的“按词表顺序找第一个命中词”循环抽为 ``_first_match``，
  城市/省份词表升为模块常量；
- 返回键映射到 ``ResearchContext`` 字段名：
  ``research_discipline`` → ``discipline``、``budget_source`` → ``budget``、
  ``target_region`` → ``extras.region``（``scale`` 同名保留）。

点号键约定：形如 ``extras.region`` 的键表示要落到 ``ResearchContext.extras``，
宿主合并字段时按 ``.`` 拆分；kernel 自身不做落库，故保持扁平 dict。
"""

import re

from fieldwork_agent.kernel.guidance.academic import academic_context_patch, location_text

# 中文城市/地名列表（用于匹配）
KNOWN_CITIES: list[str] = [
    "北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "重庆",
    "武汉", "西安", "长沙", "昆明", "贵阳", "拉萨", "乌鲁木齐",
    "银川", "西宁", "兰州", "福州", "厦门", "南宁", "桂林",
    "大理", "丽江", "香格里拉", "腾冲", "芒市", "景洪",
    "西双版纳", "腾冲", "文山", "红河", "楚雄", "玉溪",
    "遵义", "毕节", "六盘水", "安顺", "黔东南", "黔南", "黔西南",
    "澳门", "香港", "台北", "高雄", "台中",
]

# 省份关键词
PROVINCES: list[str] = [
    "云南", "贵州", "四川", "广西", "西藏", "新疆",
    "甘肃", "青海", "湖南", "湖北", "福建", "广东", "浙江",
    "江苏", "安徽", "江西", "河南", "河北", "山东", "山西",
    "陕西", "宁夏", "内蒙古",
]

# ``extract_researcher_info`` 的区域词表（沿用引擎原实现，是 PROVINCES 的前 13 项；
# 审计 F03 增补常用大区词，令“华南看看”这类跨省表述也能命中 extras.region）
_REGION_PLACES: list[str] = PROVINCES[:13] + [
    "华北", "华东", "华南", "华中", "西南", "西北", "东北",
]


def _first_match(text: str, words: list[str]) -> str | None:
    """``text`` 中出现的第一个 ``words`` 词（按词表顺序，非出现位置）。"""
    for word in words:
        if word in text:
            return word
    return None


def extract_locations(message: str, existing: dict) -> dict[str, str]:
    """从消息中提取出发城市与目的地；``existing`` 已有的键不再生成。"""
    locations: dict[str, str] = {}
    message = location_text(message)

    # ─── 提取目的地 ──────────────────────
    if not existing.get("target_location"):
        m = re.search(r"(?:田野(?:点|地点)?|调研地点|调查地点)(?:在|是|设在|：|:)\s*([^,，。；;]+)", message)
        dest_found = _first_match(m.group(1), KNOWN_CITIES + PROVINCES) if m else None

        # 模式1: "到/去/前往 + 地名"
        m = re.search(r"(?:到|去|前往|visiting)\s*([^\s,，。]+)", message)
        if m and not dest_found:
            dest_found = _first_match(m.group(1), KNOWN_CITIES + PROVINCES)

        # 模式2: "地名 + 的 + 村落/社区/乡镇"
        if not dest_found:
            m = re.search(r"([^\s,，。]+?)\s*(?:的)?(?:村落|社区|乡镇|县|镇|乡)", message)
            if m:
                dest_found = _first_match(m.group(1), KNOWN_CITIES)

        # 模式3: 直接匹配已知城市列表（学术专名已遮蔽）。
        if not dest_found:
            dest_found = _first_match(message, KNOWN_CITIES)

        if dest_found:
            locations["target_location"] = dest_found

    # ─── 提取出发城市 ──────────────────────
    if not existing.get("departure_city"):
        dep_found = None

        # 模式1: "从 + 地名 + 到/前往"
        m = re.search(r"从\s*([^\s,，。]+?)\s*(?:到|去|前往|出发)", message)
        if m:
            dep_found = _first_match(m.group(1), KNOWN_CITIES + PROVINCES)

        # 模式2: "出发城市" 关键词
        if not dep_found:
            m = re.search(r"(?:出发|from).+?([^\s,，。]+)", message)
            if m:
                dep_found = _first_match(m.group(1), KNOWN_CITIES + PROVINCES)

        if dep_found:
            locations["departure_city"] = dep_found

    return locations


def extract_researcher_info(message: str) -> dict:
    """从自然语言回复中提取可映射到 ``ResearchContext`` 的结构化信息。

    与引擎原实现等价的四类软线索（学科/规模/经费来源/区域）之外，新增
    **purpose 用途提取**（审阅意见 R1）：文体词优序 kaiti>grant>course>global；
    无命中则不填（由宿主在阶段 A 首问里显式询问）。返回扁平 dict。
    """
    info: dict[str, str] = {}
    lower = message.lower()
    semantic_text = location_text(message)

    # 学科
    discipline_text = re.sub(
        r"[^,，。；;]*(?:申请|申請|报考|報考|取向|传统|傳統|目标院系|目標院系|目标学位|目標學位|目标导师|目標導師)[^,，。；;]*",
        "", semantic_text)
    if "人类学" in discipline_text or "anthropology" in discipline_text.lower():
        info["discipline"] = "人类学"
    elif "社会学" in discipline_text or "sociology" in discipline_text.lower():
        info["discipline"] = "社会学"
    elif "政治学" in discipline_text or "politics" in discipline_text.lower():
        info["discipline"] = "政治学"
    elif "历史学" in discipline_text or "歷史學" in discipline_text or "history" in discipline_text.lower():
        info["discipline"] = "历史学"
    elif "跨学科" in discipline_text or "interdisciplinary" in discipline_text.lower():
        info["discipline"] = "跨学科"

    # 调研规模
    if "团队" in message or "多人" in message:
        info["scale"] = "团队调研"
    elif "单人" in message or "独自" in message:
        info["scale"] = "单人调研"

    # 预算关键词
    if "基金" in message or "grant" in lower:
        info["budget"] = "基金支持"
    elif "自费" in message or "self-funded" in lower:
        info["budget"] = "自费"
    else:
        m = re.search(r"(?:预算|经费|费用)[^0-9]{0,4}(\d+(?:\.\d+)?\s*[千万亿]元?)",
                      message)
        if m:
            info["budget"] = re.sub(r"\s+", "", m.group(1))

    # 用途关键词（审阅意见 R1）：先辨文体词，避免"有基金支持"误触发 grant
    if "开题" in message:
        info["purpose"] = "kaiti"
    elif "申报" in message or "活页" in message or "国家社科" in message:
        info["purpose"] = "grant"
    elif "课程" in message or "作业" in message:
        info["purpose"] = "course"
    elif ("海外" in message or "留学" in message or "申请" in message
          or "proposal" in lower or "phd" in lower):
        info["purpose"] = "global"

    # 地点关键词提取
    region = _first_match(semantic_text, _REGION_PLACES)
    if region:
        info["extras.region"] = region

    return info

# ---------------------------------------------------------------- 时间窗口（F03）

_TIME_WINDOW_RES: tuple = (
    re.compile(r"\d{4}年\d{1,2}月(?:到|至|-|—|~)\d{4}年\d{1,2}月"),
    re.compile(r"\d{4}年\d{1,2}月"),
    re.compile(r"\d{1,2}月(?:到|至|-|—|~)\d{1,2}月"),
    re.compile(r"\d{1,2}月"),
    re.compile(r"寒假期间|暑假期间|寒假|暑假|春假"),
)


def extract_time_window(message: str) -> dict[str, str]:
    """从消息中提取调研时间窗口（F03：time_window 是阶段 A→B 必需字段之一）。

    优先级即元组顺序：跨年月区间 > 单年月 > 月份区间 > 单月 > 假期词；
    每条消息只取第一个命中的模式（首段陈述优先，后续补充由已填过滤兜底）。
    """
    for pattern in _TIME_WINDOW_RES:
        m = pattern.search(message or "")
        if m:
            return {"time_window": m.group(0)}
    return {}


def apply_message_extractions(message: str, existing: dict | None = None) -> dict:
    """组合抽取器（CLI/Web 共用，F03 基石）：locations + researcher_info + time_window。

    旧槽位返回增量 patch，已填值不覆盖；``extras.*`` 保留点号形式交宿主合并。
    明确表达的学术背景允许更正或撤回，不自动修改既有地点。
    """
    existing = existing or {}
    extras_existing = existing.get("extras") or {}
    found: dict[str, str] = {}
    found.update(extract_locations(message, existing))
    for key, value in extract_researcher_info(message).items():
        if key.startswith("extras."):
            if not str(extras_existing.get(key.split(".", 1)[1], "")).strip():
                found[key] = value
        elif not str(existing.get(key, "")).strip():
            found[key] = value
    for key, value in extract_time_window(message).items():
        if not str(existing.get(key, "")).strip():
            found[key] = value
    # 学术背景只从明确表达提取，允许完整更正与显式撤回；其他旧槽位仍保持增量语义。
    found.update(academic_context_patch(message, existing))
    return found
