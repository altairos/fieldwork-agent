"""缓存索引摘要派生（spec C5，R3/R12）——Json/SQLite 双实现共用的单一真源。

写入时派生（cache_search）而非索引时：SQLite 索引查询只读元数据列，
派生放索引时就得回读全部 result blob（评审 R12 纠错）。
"""
from __future__ import annotations

from typing import Any


def result_summary(result: Any) -> tuple[bool, int | None]:
    """result → (empty, total)。

    empty = 无 items 且 total==0/缺失 且无 answer（ai-search 直答不算空，R1×R3）。
    total 非 int（缺失/漂移）→ None。截断标记 {truncated,preview} 不走这里——
    写入路径已知截断，直接 (False, None)。
    """
    if not isinstance(result, dict):
        return True, None
    items = result.get("items")
    total = result.get("total")
    answer = result.get("answer")
    total_out = total if isinstance(total, int) and not isinstance(total, bool) else None
    empty = (not (isinstance(items, list) and items)
             and not (isinstance(total_out, int) and total_out > 0)
             and not (isinstance(answer, str) and answer.strip()))
    return empty, total_out
