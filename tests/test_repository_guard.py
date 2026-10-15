# tests/test_repository_guard.py
"""kernel/repository/guard.py 表驱动单元测试：project_id 净化规则单一来源逐条钉住。

规则语义与原 MCP 薄壳 ``_pid_violation`` 逐条等价——抽取前后的等价性另由既有
``tests/test_mcp_state_tools.py`` 44 项测试（不改一行仍全绿）回归证明；本文件直接
针对 kernel 函数：合法/非法形状表 + containment 正/反例。
"""

import os
from pathlib import Path

import pytest
from fieldwork_agent.kernel.repository.guard import PID_MAX_LEN, project_id_violation

# 合法形状：字母数字下划线连字符、段内多点、版本号、中文 id、自动生成的 field_<时间戳>
LEGAL_PIDS = ["p1", "p-1_2", "study..2024", "v1.2", "田野项目2026", "field_20261015_120000"]

# 非法形状：空串/非 str/超长/正反斜杠穿越/裸点段/空段/盘符/绝对路径/null 字节
ILLEGAL_PIDS = [
    "",                          # 空串
    None,                        # 非字符串
    42,                          # 非字符串
    "a" * (PID_MAX_LEN + 1),     # 长度 >64
    "../evil",                   # 相对穿越（正斜杠）
    "..\\..\\evil",              # 相对穿越（反斜杠）
    ".",                         # 裸点段（root/"." 解析回根本身，污染 projects 根）
    "./x",                       # 点段 + 子路径
    "a//b",                      # 空段退化形
    "C:\\evil",                  # Windows 盘符绝对路径
    "C:evil",                    # 盘符相对路径
    "/etc/passwd",               # Unix 绝对路径
    "p\x00id",                   # null 字节
    "..",                        # 裸穿越段
]

ILLEGAL_IDS = ["空串", "None", "整数", "超长65", "穿越正斜杠", "穿越反斜杠", "点",
               "点斜杠", "空段", "盘符绝对", "盘符相对", "unix绝对", "null字节", "点点"]


@pytest.mark.parametrize("pid", LEGAL_PIDS)
def test_legal_shapes_return_empty(pid):
    """合法形状返回空串（段内点号、中文 id、field_<时间戳> 均不误伤）。"""
    assert project_id_violation(pid) == ""


@pytest.mark.parametrize("pid", ILLEGAL_PIDS, ids=ILLEGAL_IDS)
def test_illegal_shapes_return_reason(pid):
    """非法形状返回非空中文原因（宿主据此折 MCP invalid_args / HTTP 400 包络）。"""
    reason = project_id_violation(pid)
    assert isinstance(reason, str) and reason != ""


def test_containment_accepts_legal_id_inside_root(tmp_path):
    """containment 正例：给了 root 时，root 内的合法 id 仍通过（双保险不误伤）。"""
    root = tmp_path / "projects"
    root.mkdir()
    assert project_id_violation("p1", root) == ""


def _make_escape_link(root: Path, target: Path) -> Path:
    """在 root 内造一个指向 root 之外 target 的"逃逸口"链接，名为 ``evil``。

    Windows 用 Junction（无需管理员特权；symlink 在无特权时报 WinError 1314），
    POSIX 用 symlink。两者均使 ``(root/"evil").resolve()`` 落到 root 之外——
    字符规则放行的 "evil" 只能被 containment 双保险这一层拦住。
    """
    target.mkdir(parents=True, exist_ok=True)
    link = root / "evil"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target)
    return link


def test_containment_rejects_id_resolving_outside_root(tmp_path):
    """containment 反例：id 字符规则合法（"evil"）但经链接解析到 root 之外 → 拒绝。"""
    root = tmp_path / "projects"
    root.mkdir()
    try:
        _make_escape_link(root, tmp_path / "outside")
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("当前环境不支持 Junction/symlink，containment 反例无法构造")
    assert project_id_violation("evil", root) != ""
