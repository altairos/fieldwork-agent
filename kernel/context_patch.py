"""研究上下文 patch 共享严格校验（审计二 R05 单一来源）。

Web 回合工具（``web_host/agent/state_tools.py``）、MCP
``update_research_context``、REST ``PUT /research-context`` 三入口统一走
``sanitize_context_patch``：白名单字段、类型严格、空白拒绝、
``interviews_not_applicable=True`` 需同时给理由（patch 内或 current 已有）。
任一字段非法即整次拒绝（原子语义），绝不部分写入。
"""
from fieldwork_agent.kernel.models import ACADEMIC_CONTEXT_FIELDS, InterviewState, ResearchNote
from fieldwork_agent.kernel.research_state import (
    prepare_interview_state,
    prepare_notes,
    question_fingerprint,
)

# title（任务1）：项目显示名——用户改名（REST）与 agent 自动命名共用此白名单入口

def _nonempty(v) -> bool:
    return isinstance(v, str) and v.strip()


ALLOWED_CONTEXT_FIELDS = (
    "title",
    "discipline", "topic", "site_status", "scale", "budget",
    "time_window", "departure_city", "target_location", "travel_date",
    "purpose", "research_questions", "interviews_not_applicable",
    "interviews_na_reason",
    "candidate_windows",
    "research_notes", "interview_state",
    *ACADEMIC_CONTEXT_FIELDS,
    "clear_fields",  # 写入动作，净化后转换为空值；不进入 ResearchContext。
)


def context_fields_hint() -> str:
    """白名单字段的提示文案（错误 hint 用），顿号分隔。"""
    return "、".join(ALLOWED_CONTEXT_FIELDS)


def context_patch_schema() -> dict:
    """两种状态工具共用可见结构，避免模型猜笔记kind或对象字段。"""
    note = ResearchNote.model_json_schema()
    note["properties"].pop("scope_fingerprint", None)
    note["additionalProperties"] = False
    note["description"] = "新增须有content/origin；按已有id更新状态；kind/status只用枚举值，采纳/否定需basis"
    memory = InterviewState.model_json_schema()
    memory["additionalProperties"] = False
    return {"type": "object", "additionalProperties": False, "minProperties": 1,
            "properties": {name: (
                {"type": "array", "minItems": 1, "uniqueItems": True,
                 "items": {"type": "string", "enum": list(ACADEMIC_CONTEXT_FIELDS)},
                 "description": "用户明确撤回的学术背景字段；不能与同名新值同时提交"}
                if name == "clear_fields" else
                {"type": "array", "minItems": 1, "maxItems": 8, "items": note} if name == "research_notes"
                else memory if name == "interview_state"
                else {"type": "boolean"} if name == "interviews_not_applicable"
                else {"type": "array", "items": {"type": "object"}} if name == "candidate_windows"
                else {"type": "string", "description": "清空已确认问题须同时记录同文的rejected问题笔记及basis"}
                if name == "research_questions"
                else {"type": "string", "minLength": 1}) for name in ALLOWED_CONTEXT_FIELDS}}


def sanitize_context_patch(patch: dict, current: dict | None = None) -> dict:
    """校验并净化研究上下文 patch；非法即 raise ValueError（文案含全部错误）。

    ``current`` 为当前已存上下文（仅用于 NA 理由的存量豁免）。
    """
    current = current or {}
    if not isinstance(patch, dict) or not patch:
        raise ValueError("patch 为空：至少一个白名单字段且值为非空字符串或布尔")
    errors = []
    structured = {}
    withdrawing_question = False
    for key, value in patch.items():
        if key not in ALLOWED_CONTEXT_FIELDS:
            errors.append(f"未知字段：{key}")
            continue
        if key == "clear_fields":
            if (not isinstance(value, list) or not value
                    or any(not isinstance(item, str) or item not in ACADEMIC_CONTEXT_FIELDS
                           for item in value)):
                errors.append("clear_fields须为非空学术背景字段名列表")
            elif len(value) != len(set(value)) or any(item in patch for item in value):
                errors.append("clear_fields不得重复或与本次设置字段重叠")
            continue
        if key in {"research_notes", "interview_state"}:
            try:
                structured[key] = (prepare_notes(value, current, {**current, **patch})
                                   if key == "research_notes" else prepare_interview_state(value, current))
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(f"{key}校验失败：{exc}")
            continue
        if key == "candidate_windows":
            if (not isinstance(value, list) or not value
                    or not all(isinstance(w, dict) and _nonempty(w.get("start"))
                               and _nonempty(w.get("end")) for w in value)):
                errors.append("candidate_windows 须为非空列表，每项含非空 start/end")
            continue
        if key == "interviews_not_applicable":
            if not isinstance(value, bool):
                errors.append(f"{key} 须为布尔")
            elif value and not str(
                patch.get("interviews_na_reason")
                or current.get("interviews_na_reason") or ""
            ).strip():
                errors.append(
                    "interviews_na_reason 需与 interviews_not_applicable=True "
                    "同时提供（存量已有理由则豁免）")
            continue
        if not isinstance(value, str):
            errors.append(f"{key} 须为字符串")
        elif not value.strip():
            if key == "research_questions":
                withdrawing_question = True
            else:
                errors.append(f"{key} 不得为空白字符串")
    old_question = str(current.get("research_questions") or (current.get("extras") or {}).get("research_questions") or "").strip()
    if withdrawing_question and not (old_question and any(
            note["kind"] == "question" and note["status"] == "rejected"
            and note["content"].strip() == old_question and note["basis"].strip()
            for note in structured.get("research_notes", []))):
        errors.append("撤回research_questions须同时记录与旧问题同文的rejected问题笔记及用户依据")
    if errors:
        raise ValueError("；".join(errors))
    clean = {k: (structured[k] if k in structured else v if k in ("interviews_not_applicable", "candidate_windows")
                else v.strip())
            for k, v in patch.items() if k != "clear_fields"}
    clean.update({name: "" for name in patch.get("clear_fields", [])})
    if withdrawing_question and "research_questions" in (current.get("extras") or {}):
        clean["extras"] = {key: value for key, value in current["extras"].items() if key != "research_questions"}
    if "research_questions" in clean:
        clean["research_question_scope"] = question_fingerprint({**current, **clean})
    elif ("topic" in clean and clean["topic"] != current.get("topic")
          and (current.get("research_questions") or (current.get("extras") or {}).get("research_questions"))):
        clean["research_question_scope"] = current.get("research_question_scope") or question_fingerprint(current)
    return clean
