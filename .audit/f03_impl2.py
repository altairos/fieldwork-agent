# -*- coding: utf-8 -*-
"""F03 步骤 3.4/3.5 续：context_patch.py 四处 + loop.py 一处（models.py 已完成）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def apply(rel: str, old: str, new: str) -> None:
    target = ROOT / rel
    text = target.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    if crlf:
        text = text.replace("\r\n", "\n")
    n = text.count(old)
    assert n == 1, f"{rel}: old block found {n} times (expect 1)"
    assert new not in text, f"{rel}: new block already present"
    text = text.replace(old, new, 1)
    assert text.count(new) == 1
    if crlf:
        text = text.replace("\n", "\r\n")
    target.write_bytes(text.encode("utf-8"))
    print(f"OK {rel} (crlf={crlf})")


# ---- 3.4a 白名单扩容：interviews_na_reason 之后 ----
apply(
    "kernel/context_patch.py",
    '''    "interviews_na_reason",
)''',
    '''    "interviews_na_reason",
    "candidate_windows",
)''',
)

# ---- 3.4b(辅助) 模块顶部 _nonempty（docstring 之后、ALLOWED 之前） ----
apply(
    "kernel/context_patch.py",
    '''kernel 只依赖标准库。
"""

ALLOWED_CONTEXT_FIELDS = (''',
    '''kernel 只依赖标准库。
"""


def _nonempty(v) -> bool:
    return isinstance(v, str) and v.strip()


ALLOWED_CONTEXT_FIELDS = (''',
)

# ---- 3.4b sanitize 循环形状校验 ----
apply(
    "kernel/context_patch.py",
    '''        if key not in ALLOWED_CONTEXT_FIELDS:
            errors.append(f"未知字段：{key}")
            continue
        if key == "interviews_not_applicable":''',
    '''        if key not in ALLOWED_CONTEXT_FIELDS:
            errors.append(f"未知字段：{key}")
            continue
        if key == "candidate_windows":
            if (not isinstance(value, list) or not value
                    or not all(isinstance(w, dict) and _nonempty(w.get("start"))
                               and _nonempty(w.get("end")) for w in value)):
                errors.append("candidate_windows 须为非空列表，每项含非空 start/end")
            continue
        if key == "interviews_not_applicable":''',
)

# ---- 3.4c 返回推导跳过 strip ----
apply(
    "kernel/context_patch.py",
    '''    return {k: (v if k == "interviews_not_applicable" else v.strip())
            for k, v in patch.items()}''',
    '''    return {k: (v if k in ("interviews_not_applicable", "candidate_windows")
                else v.strip())
            for k, v in patch.items()}''',
)

# ---- 3.5 loop.py 时窗回写块替换 ----
apply(
    "web_host/agent/loop.py",
    '''                if name == "analyze_fieldwork_window" and env.ok:
                    dest = str(call_args_map.get(call_id, {}).get("destination", ""))
                    cid = repo.cache_search(pid, f"time_window::{dest}",
                                            "analyze_fieldwork_window", data)
                    snapshot_ids.append(cid)
                    # spec §5.3：推荐时窗确定性回写 ResearchContext.time_window
                    w = (data.get("windows") or [{}])[0]
                    if w.get("start") and w.get("end"):
                        reason = (w.get("reasons") or [""])[0]
                        merged = repo.get_research_context(pid)
                        merged["time_window"] = (
                            f'{w["start"]} ~ {w["end"]}'
                            f'（推荐时窗{("：" + reason) if reason else ""}）'
                        )
                        repo.update_research_context(pid, merged)''',
    '''                if name == "analyze_fieldwork_window" and env.ok:
                    dest = str(call_args_map.get(call_id, {}).get("destination", ""))
                    cid = repo.cache_search(pid, f"time_window::{dest}",
                                            "analyze_fieldwork_window", data)
                    snapshot_ids.append(cid)
                    # F03：推荐时窗与用户约束分离——写 candidate_windows
                    # （候选，未经用户确认），time_window 只留用户确认值
                    wins = [w for w in (data.get("windows") or [])
                            if w.get("start") and w.get("end")][:3]
                    if wins:
                        payload = [
                            {"start": w["start"], "end": w["end"],
                             "reason": (w.get("reasons") or [""])[0]}
                            for w in wins
                        ]
                        repo.update_research_context(
                            pid, {"candidate_windows": payload})''',
)

print("ALL OK")
