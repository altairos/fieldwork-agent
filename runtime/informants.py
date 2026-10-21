"""访谈对象登记服务：Web 工具、REST 采纳与 CLI 共用同一编号/去重规则（P2-1）。

此前几条路径各自为政：工具按 ``type`` 去重（同类型第二位静默跳过，现场常见的
“两位村干部”根本登记不进去）、编号用 ``len(records)+len(staged)+1``（回合进行中
用户点右栏「采纳登记」时两边可能同时得到 ``INF-01``）；REST 则允许同类型多条。
本模块把校验、编号、去重与写入收成一处：

- 编号按既有 ``INF-xx`` 的最大序号 +1（不按条数推算，删除记录后不会撞号）；
- 去重只挡**完全相同的档案**（type+relation+contact+notes）；同类型不同接近
  策略照常登记——去重是为工具重复调用幂等，不是类型白名单；
- 进程内按项目加锁，读-算号-写三步不交错（跨进程仍靠仓储文件锁；极端并发
  可能撞号，但不影响正文生成）；
- 单次最多 ``MAX_INFORMANTS_PER_CALL`` 条、字段长度有界（工具 schema 同步约束，
  防止一次写入 30 条把右栏与后续上下文撑爆）。

REST 的「采纳登记」是用户逐条点击的手动动作，``dedupe=False``：同一份候选
点两次仍会生成两条记录（服务端不替用户判断“是不是同一个人”）。
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from threading import Lock
from typing import Any

from fieldwork_agent.kernel.documents.informants import candidate_to_record
from fieldwork_agent.kernel.models import InformantCandidate
from pydantic import ValidationError

# 单次登记的条数上限（与 _SAVE_INFORMANTS_SCHEMA.maxItems 同源）
MAX_INFORMANTS_PER_CALL = 6
# 字段长度上限（schema 里也有 maxLength；这里兜底模型不守 schema 的情况）
MAX_TYPE_CHARS = 40
MAX_TEXT_CHARS = 400
MAX_DOMAIN_CHARS = 60
MAX_DOMAINS = 8

_CODE_RE = re.compile(r"INF-(\d+)$")
_locks: dict[str, Lock] = {}
_locks_guard = Lock()


def _lock_for(pid: str) -> Lock:
    """项目级进程内锁（同项目的读-算号-写串行）。"""
    with _locks_guard:
        return _locks.setdefault(pid, Lock())


def next_informant_seq(records: Iterable[dict]) -> int:
    """下一个登记序号：既有 ``INF-xx`` 最大值 +1（无编号记录按条数兜底）。"""
    max_seq = 0
    count = 0
    for record in records:
        count += 1
        match = _CODE_RE.match(str(record.get("code") or "").strip())
        if match:
            max_seq = max(max_seq, int(match.group(1)))
    return max(max_seq, count) + 1


def _fingerprint(record: dict) -> tuple[str, str, str, str]:
    """档案内容指纹（不含编号/时间）：完全相同的四条视为重复登记。"""
    return tuple(" ".join(str(record.get(key) or "").split())
                 for key in ("type", "relation", "contact", "notes"))


def _normalize(raw: Any) -> tuple[InformantCandidate | None, str]:
    """校验并截断候选字段；返回 (候选, 跳过原因)。"""
    try:
        candidate = InformantCandidate.model_validate(raw)
    except ValidationError as exc:
        return None, f"invalid: {exc.errors()[0].get('msg', '')}"
    candidate.type = str(candidate.type or "").strip()[:MAX_TYPE_CHARS]
    if not candidate.type:
        return None, "missing_type"
    for key in ("approach", "cautions", "ethics", "access_channel", "preparation"):
        setattr(candidate, key, str(getattr(candidate, key) or "").strip()[:MAX_TEXT_CHARS])
    candidate.question_domains = [
        str(domain).strip()[:MAX_DOMAIN_CHARS]
        for domain in candidate.question_domains[:MAX_DOMAINS]
        if str(domain).strip()
    ]
    return candidate, ""


def register_informants(repo, pid: str, candidates: list, *,
                        buffer=None, dedupe: bool = True) -> dict:
    """登记一批候选，返回 ``{"saved": [...], "skipped": [...], "total": n}``。

    ``saved`` 项含 ``code/type``（直接落盘时另有 ``id``）；``total`` 是登记后
    项目里的访谈对象总数（仓储 + 本轮暂存 + 本次新增）。
    ``buffer`` 非空时写入回合暂存区（轮末 commit / 取消 discard）。
    """
    existing = [*repo.list_informants(pid),
                *(buffer.staged_informants() if buffer is not None else [])]
    if not isinstance(candidates, list) or not candidates:
        return {"saved": [], "skipped": [], "total": len(existing)}

    saved: list[dict] = []
    skipped: list[dict] = []
    records: list[dict] = []
    with _lock_for(pid):
        # 锁内重读：并发的 REST / 工具调用可能刚写入新记录
        base = [*repo.list_informants(pid),
                *(buffer.staged_informants() if buffer is not None else [])]
        seq = next_informant_seq(base)
        seen = {_fingerprint(record) for record in base} if dedupe else set()
        for index, raw in enumerate(candidates):
            if index >= MAX_INFORMANTS_PER_CALL:
                skipped.append({"type": "", "reason": "over_limit"})
                continue
            candidate, reason = _normalize(raw)
            if candidate is None:
                skipped.append({"type": "", "reason": reason})
                continue
            record = candidate_to_record(candidate, seq)
            fingerprint = _fingerprint(record)
            if dedupe and fingerprint in seen:
                skipped.append({"type": candidate.type, "reason": "duplicate"})
                continue
            seq += 1
            if buffer is not None:
                buffer.stage_informant(record)
                entry = {"code": record["code"], "type": record["type"]}
            else:
                rid = repo.save_informant(pid, record)
                entry = {"code": record["code"], "type": record["type"], "id": rid}
            seen.add(fingerprint)
            records.append(record)
            saved.append(entry)
        total = len(base) + len(saved)
    return {"saved": saved, "skipped": skipped, "total": total,
            "records": records}
