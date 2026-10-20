# kernel/repository/confirm_hash.py
"""方案确认依赖指纹（审计二 R09 + 评审 R02）。

确认语义从"确认最新一份产物"收窄为"确认指定文档 + 当时五路依赖指纹"：
研究上下文、田野点、报道人、知识库条目、行程。任一路变化
→ 指纹不一致 → 旧确认失效。哈希仅用于失效判定，不承担安全职责
（与 kernel.models.compute_inputs_hash 同级声明）。

两代指纹并存（评审 R02）：

- ``compute_doc_hash``（新）：五路全部取**正文实际采用的输入**，行程取内容而不是
  “当前最新快照 id”。保存时按生成输入算，确认时按项目当前已采纳状态算，
  两边不一致即拒——正文用了项目未采纳的田野点/报道人/知识/行程时无法被认证。
- ``compute_confirm_hash``（旧）：行程一路只装“最新快照 id”，仅保留用于识别
  存量文档的旧指纹（确认时兼容读取，写出侧已不再用它）。

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
    """旧代五路依赖指纹（行程只装最新快照 id）；仅供存量文档兼容读取。

    工作笔记/对话记忆不进文档事实渠道，正式上下文仍保守失效。
    """
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


# 新代指纹的形状标记：与旧代 payload 不可能碰撞（旧代没有该键）
_DOC_INPUTS_SCHEMA = "doc_inputs_v2"


def compute_doc_hash(ctx: dict, sites: list, informants: list,
                     knowledge: list, itinerary=None) -> str:
    """方案正文五渠道生成输入指纹（评审 R02）。

    每路都取**正文实际采用的输入**：ctx 为合并后的生成 ctx，田野点/报道人/
    知识库为正文引用到的列表，行程为其内容（dict/None）——而不是"当前最新
    快照 id"。于是：

    - 正文用了项目未采纳的田野点/报道人/知识/行程 → 与确认时的项目状态不同 → 拒确认；
    - 生成后又新增/替换了行程 → 内容不同 → 旧确认失效（不再依赖 id 是否变）；
    - 同轮先后生成多份行程 → 指纹绑定正文用的那一份，而不是保存时最新的那份。
    """
    payload = {
        "schema": _DOC_INPUTS_SCHEMA,
        "ctx": _canonical({k: v for k, v in (ctx or {}).items()
                           if k not in {"research_notes", "interview_state"}}),
        "sites": _canonical(sites or []),
        "informants": _canonical(informants or []),
        "knowledge": _canonical(knowledge or []),
        "itinerary": _canonical(itinerary),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()
