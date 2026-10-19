"""研究范围指纹与工作状态校验；不依赖宿主/仓储，不把候选自动采纳为事实。"""
import hashlib
import json
from uuid import uuid4

from fieldwork_agent.kernel.models import InterviewState, ResearchNote

RESEARCH_SCOPE_FIELDS = ("topic", "research_questions", "discipline", "target_location")
WORKING_STATE_FIELDS = {"research_notes", "interview_state"}
_DEFERRED_FIELDS = {
    "purpose", "discipline", "topic", "research_questions", "target_location", "site_status",
    "scale", "budget", "time_window", "departure_city", "travel_date",
}


def scope_fingerprint(ctx: dict) -> str:
    extras = ctx.get("extras") or {}
    scope = {key: " ".join(str(ctx.get(key) or extras.get(key) or "").split())
             for key in RESEARCH_SCOPE_FIELDS}
    return hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def question_fingerprint(ctx: dict) -> str:
    return scope_fingerprint({key: ctx.get(key) or (ctx.get("extras") or {}).get(key)
                              for key in ("topic", "research_questions")})


def prepare_notes(value, current: dict, scope: dict) -> list[dict]:
    """按id增量添加/改状态，保留旧候选与否定方向；不静默截断历史。"""
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError("research_notes须为1-8条笔记的列表")
    stored = [dict(note) for note in current.get("research_notes") or []]
    positions = {note.get("id"): i for i, note in enumerate(stored)}
    for raw in value:
        if not isinstance(raw, dict) or set(raw) - set(ResearchNote.model_fields):
            raise ValueError("research_notes含未知字段或非对象条目")
        previous = stored[positions[raw["id"]]] if raw.get("id") in positions else {}
        if previous and "origin" in raw and raw["origin"] != previous["origin"]:
            raise ValueError("笔记来源不可改写，请新增条目并标记旧条目被替代")
        if previous and "content" in raw and raw["content"].strip() != previous["content"]:
            raise ValueError("改写笔记请新增条目，并将旧条目标为superseded，保留修订轨迹")
        if (previous.get("status") in {"adopted", "rejected"}
                and raw.get("status", previous["status"]) != previous["status"]
                and not str(raw.get("basis") or "").strip()):
            raise ValueError("修改已采纳/否定的决定必须提供新的basis，不能静默恢复旧方向")
        data = {**previous, **raw}
        if not data.get("origin") or not isinstance(data.get("content"), str) or not data["content"].strip():
            raise ValueError("研究笔记必须明确origin及非空content")
        data["content"] = data["content"].strip()
        data["id"] = previous.get("id") or data.get("id") or f"rn_{uuid4().hex[:16]}"
        data["scope_fingerprint"] = previous.get("scope_fingerprint") or scope_fingerprint(scope)
        note = ResearchNote.model_validate(data).model_dump()
        if note["status"] in {"adopted", "rejected"} and not note["basis"].strip():
            raise ValueError("采纳/否定笔记必须提供basis，记录用户明确表达的依据")
        if note["origin"] == "literature" and not note["references"]:
            raise ValueError("文献来源笔记必须提供可追溯references")
        if any(not isinstance(ref, str) or not ref.strip() or len(ref) > 500 for ref in note["references"]):
            raise ValueError("references须为非空短字符串")
        if previous:
            stored[positions[note["id"]]] = note
        else:
            positions[note["id"]] = len(stored)
            stored.append(note)
    if len(stored) > 64:
        raise ValueError("研究笔记已达64条上限，请整理后再记录，不自动删除历史")
    return stored


def prepare_interview_state(value, current: dict) -> dict:
    if not isinstance(value, dict) or not value or set(value) - set(InterviewState.model_fields):
        raise ValueError("interview_state须为已知字段组成的非空对象")
    merged = {**(current.get("interview_state") or {}), **value}
    state = InterviewState.model_validate(merged).model_dump()
    if any(field not in _DEFERRED_FIELDS for field in state["deferred_fields"]):
        raise ValueError("deferred_fields含未知研究字段")
    if any(not question.strip() or len(question) > 300 for question in state["open_questions"]):
        raise ValueError("open_questions须为非空短问题")
    return state


def turn_memory_patch(ctx: dict, user_text: str, assistant_text: str) -> dict:
    def excerpt(text):
        return text if len(text) <= 1100 else text[:1100] + "…（节选）"
    state = {**(ctx.get("interview_state") or {}),
             "last_user_message": excerpt(user_text), "last_assistant_message": excerpt(assistant_text)}
    return {"interview_state": prepare_interview_state(state, ctx)}
