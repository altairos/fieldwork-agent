"""显式学术背景的保守规则抽取；不做实体归一化或学术关系推断。

规则只处理明确格式，复杂表达留给共用状态工具。歧义简称原样保留，
否定、疑问和假设不自动变成用户选择；地点保护也供旧抽取器共用。
"""
import re

from fieldwork_agent.kernel.context_patch import sanitize_context_patch

_NAME_CHAR = r"[\u4e00-\u9fffA-Za-z·]"
_NAME_BREAK = r"申请|报考|出发|田野|调研|取向|打算|计划|前往|来自|我是|想|从|到|去|在|是|的|和|但|而|不"
_INSTITUTION = rf"(?:(?!(?:{_NAME_BREAK})){_NAME_CHAR}){{2,30}}(?:大学|大學|学院|學院|研究院|研究所)"
_ACADEMIC_NAME = re.compile(rf"{_INSTITUTION}|(?:(?!(?:{_NAME_BREAK})){_NAME_CHAR}){{2,20}}(?:学派|學派)")
_SCHOOL = re.compile(rf"{_INSTITUTION}|港中文|香港中大|中大|CUHK\b|HKU\b", re.IGNORECASE)
_DEPARTMENT = re.compile(r"([\u4e00-\u9fffA-Za-z·]{1,20}(?:学系|學系|系|学部|學部|学院|學院))")
_DEGREE = re.compile(r"博士|硕士|碩士|本科|Ph\.?D\.?|master(?:'s)?", re.IGNORECASE)
_SPLIT = re.compile(r"[，,。；;！!\n]|而是|但是|不过|但")
_UNCERTAIN = re.compile(r"不是|并非|並非|不再|不要|尚未|不确定|不確定|不知道|不清楚|未定|待定|还没|還沒|是否|如果|假如|可能|推荐|推薦|[？?]")
_NEGATIVE_PREFIX = re.compile(r"不|没|沒|别|別|勿|取消|撤回|放弃|放棄|无意|無意")
_OTHER_OR_EXPLORATORY = re.compile(r"他|她|你|作者|老师|老師|导师|導師|朋友|同学|同學|有人|文章|书中|書中|论文|論文|例如|比如|建议|建議|考虑|考慮|了解|听说|聽說")
_REPORTED = re.compile(r"(?:他|她|老师|老師|导师|導師|朋友|同学|同學|作者|文章|论文|論文).{0,8}(?:说|說|建议|建議|推荐|推薦)")
_HYPOTHETICAL = re.compile(r"如果|假如|假设|假設")
_SELF_ASSERTION = re.compile(r"^我(?:的|决定|決定|确定|確定|要|想|准备|準備|申请|申請|取向)")
_VALUE = r"(?:是|为|為|改为|改為|改成|调整为|調整為|：|:)\s*"
_ORIENTATION = re.compile(rf"(?:研究取向|学术取向|學術取向|理论取向|理論取向|取向|学术传统|學術傳統){_VALUE}(.+)")
_ADVISOR = re.compile(rf"(?:目标导师|目標導師|意向导师|意向導師|关注的导师|關注的導師){_VALUE}(.+)")
_DEPARTMENT_VALUE = re.compile(rf"(?:目标院系|目標院系){_VALUE}(.+)")
_DEGREE_VALUE = re.compile(rf"(?:目标学位|目標學位){_VALUE}(.+)")
_INSTITUTION_VALUE = re.compile(rf"(?:目标院校|目標院校|申请院校|申請院校){_VALUE}(.+)")
_APPLICATION = re.compile(r"申请|申請|报考|報考|改申|(?:目标院校|目標院校|申请院校|申請院校)(?:是|为|為|改为|改為|改成|：|:)")
_CLEAR_LABELS = {
    "research_orientation": r"学术取向|學術取向|研究取向|取向",
    "target_institution": r"目标院校|目標院校|申请院校|申請院校",
    "target_department": r"目标院系|目標院系",
    "target_degree": r"目标学位|目標學位",
    "target_advisor": r"目标导师|目標導師|意向导师|意向導師",
}


def location_text(message: str) -> str:
    """遮蔽书名、院校和学派专名中的地名，保持其余文本的位置与分隔符。"""
    text = re.sub(r"《[^》]*》", lambda match: " " * len(match[0]), message)
    text = _ACADEMIC_NAME.sub(lambda match: " " * len(match[0]), text)
    return _SCHOOL.sub(lambda match: " " * len(match[0]), text)


def _asserted_clauses(message: str):
    """逗号不终止转述/假设语境；明确转回用户自己的陈述后才恢复抽取。"""
    message = re.sub(r"《[^》]*》", lambda match: " " * len(match[0]), message)
    for sentence in re.split(r"[。；;！!\n]", message):
        blocked = False
        for clause in _SPLIT.split(sentence):
            clause = clause.strip()
            if _SELF_ASSERTION.match(clause):
                blocked = False
            if _REPORTED.search(clause) or _HYPOTHETICAL.search(clause):
                blocked = True
            if not blocked:
                yield clause


def academic_context_patch(message: str, current: dict | None = None) -> dict:
    """返回经过共用校验器的正式字段 patch；明确重述可更正旧值。"""
    patch: dict = {}
    current = current or {}
    cleared: set[str] = set()
    for clause in _asserted_clauses(message):
        # 明确撤回既有申请时，同时撤回该申请的院系、学位和导师。
        # 提及别的学校或转述他人建议不能清空当前目标；歧义简称不自动等同。
        withdrawal = re.search(r"(?:不再|取消|撤回)(?:申请|申請|报考|報考)(.*?)(?:了)?$", clause)
        if (withdrawal and not re.search(r"如果|假如|是否|[？?]", clause)
                and not _OTHER_OR_EXPLORATORY.search(clause[:withdrawal.start()])
                and not _NEGATIVE_PREFIX.search(clause[:withdrawal.start()])):
            named = _SCHOOL.match(withdrawal[1].strip())
            existing = patch.get("target_institution", current.get("target_institution"))
            if existing and (not withdrawal[1].strip() or named and named[0] == existing):
                for field in ("target_institution", "target_department", "target_degree", "target_advisor"):
                    cleared.add(field)
                    patch.pop(field, None)
        if not clause or _UNCERTAIN.search(clause):
            continue
        for field, labels in _CLEAR_LABELS.items():
            clear = re.search(rf"(?:取消|清空|撤回|删除|刪除)(?:我的)?(?:{labels})(?:了)?$", clause)
            if (clear and not _OTHER_OR_EXPLORATORY.search(clause[:clear.start()])
                    and not _NEGATIVE_PREFIX.search(clause[:clear.start()])):
                cleared.add(field)
                patch.pop(field, None)
        for field, pattern in (("research_orientation", _ORIENTATION), ("target_advisor", _ADVISOR),
                               ("target_department", _DEPARTMENT_VALUE), ("target_degree", _DEGREE_VALUE),
                               ("target_institution", _INSTITUTION_VALUE)):
            match = pattern.search(clause)
            if (match and not _NEGATIVE_PREFIX.search(clause[:match.start()])
                    and not _OTHER_OR_EXPLORATORY.search(clause[:match.start()])):
                patch[field] = match[1].strip()
                cleared.discard(field)
        correction = re.search(r"(?:研究取向|学术取向|取向)从.+?改为(.+)$", clause)
        if (correction and not _OTHER_OR_EXPLORATORY.search(clause[:correction.start()])
                and not _NEGATIVE_PREFIX.search(clause[:correction.start()])):
            patch["research_orientation"] = correction[1].strip()
            cleared.discard("research_orientation")
        application = _APPLICATION.search(clause)
        if (application is None or _NEGATIVE_PREFIX.search(clause[:application.start()])
                or _OTHER_OR_EXPLORATORY.search(clause[:application.start()])):
            continue
        tail = clause[application.end():].strip()
        tail = re.sub(r"^(?:改为|改成|换成|換成|的是|的|是)\s*", "", tail)
        school = _SCHOOL.match(tail)
        if school:
            patch["target_institution"] = school[0]
            department = _DEPARTMENT.match(tail[school.end():])
            if department:
                patch["target_department"] = department[1]
        degree = _DEGREE.search(tail)
        if degree:
            patch["target_degree"] = degree[0]
        cleared.difference_update(patch)
    if cleared:
        patch["clear_fields"] = sorted(cleared)
    return sanitize_context_patch(patch) if patch else {}
