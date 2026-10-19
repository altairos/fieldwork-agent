# kernel/repository/confirm_hash.py
"""方案确认依赖指纹（审计二 R09）：五路输入 → sha1。

确认语义从"确认最新一份产物"收窄为"确认指定文档 + 当时五路依赖指纹"：
研究上下文、田野点、报道人、知识库条目、最新行程快照 id。任一路变化
→ 指纹不一致 → 旧确认失效。哈希仅用于失效判定，不承担安全职责
（与 kernel.models.compute_inputs_hash 同级声明）。

``ProposalConfirmError.code`` 四类：``not_found``（文档不存在）、
``wrong_kind``（非 proposal 文档）、``dep_hash_missing``（legacy 文档无指纹）、
``dep_hash_mismatch``（依赖已变化）。
"""
import hashlib
import json


class ProposalConfirmError(Exception):
    """confirm_proposal 的业务拒绝（审计二 R09）：code 见模块 docstring。"""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _canonical(value):
    """递归规范化：float 整值量化为 int（100 与 100.0 指纹必须一致）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def compute_confirm_hash(ctx: dict, sites: list, informants: list,
                         knowledge: list, itinerary_cid: str = "") -> str:
    """五路依赖指纹；工作笔记/对话记忆不进文档事实渠道，正式上下文仍保守失效。"""
    payload = {
        "ctx": _canonical({k: v for k, v in (ctx or {}).items()
                           if k not in {"research_notes", "interview_state"}}),
        "sites": _canonical(sites or []),
        "informants": _canonical(informants or []),
        "knowledge": _canonical(knowledge or []),
        "itinerary_cid": itinerary_cid or "",
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()
