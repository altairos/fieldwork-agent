"""Q02：同一模块内顶层定义不得重名——后定义会静默覆盖前定义。

Python 不报错，行为由「最后一次定义」决定：合并/搬移代码时少删一份，调用点
就会悄悄换实现。2026-10-20 审计发现 `web_host/agent/loop.py` 的
`_stop_requested` 定义两次（128 / 771），后者覆盖前者。
"""

import ast
import pathlib

PACKAGES = ("core", "kernel", "runtime", "mcp_server", "web_host")
ROOT = pathlib.Path(__file__).resolve().parent.parent


def _duplicates(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    dups = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in seen:
                dups.append((node.name, seen[node.name], node.lineno))
            else:
                seen[node.name] = node.lineno
    return dups


def test_no_duplicate_top_level_definitions():
    problems = []
    for package in PACKAGES:
        for path in (ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for name, first, second in _duplicates(path):
                problems.append(f"{path.relative_to(ROOT)}: {name} "
                                f"定义两次（{first} 与 {second}）")
    assert not problems, "重复的顶层定义会静默覆盖：\n" + "\n".join(problems)
