# kernel/identity/owner_resolve.py
"""共享归属解析：X-Owner-Token 裸值 → owner 字符串（Web + HTTP MCP 复用）。"""
from __future__ import annotations


class SessionExpired(Exception):
    """sess_ 令牌解析失败（过期/撤销/未知）——可区分于「无令牌/匿名」（R15）。"""

    def __init__(self, raw: str):
        self.raw = raw
        super().__init__("session expired or invalid")


def resolve_owner(raw: str, user_store) -> str | None:
    """调用方已对 raw 过 X-Owner-Token 形状校验。

    - `sess_` 前缀：会话命中 → `user:<id>`；失效/无 store → 抛 SessionExpired（不静默折 None）；
    - 其他（匿名 32-hex）：`anon:<raw>`（零 DB 命中）。
    """
    if raw.startswith("sess_"):
        uid = user_store.resolve_session(raw) if user_store is not None else None
        if not uid:
            raise SessionExpired(raw)
        return f"user:{uid}"
    return f"anon:{raw}"
