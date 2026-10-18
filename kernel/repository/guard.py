"""路径 ID 守卫（审计 F01）：project_id / turn_id 校验的单一实现来源。

mcp_server（折 tool 错误包络）与 web_host（折 HTTPException）两个外壳薄壳
复用，规则变更只改本文件。kernel 只依赖标准库。
"""

import re
from typing import Any

PID_MAX_LEN = 64
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
TURN_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def path_id_violation(
    value: Any,
    *,
    field: str = "project_id",
    root: Any = None,
    max_len: int = PID_MAX_LEN,
) -> str:
    """用作路径段的 ID 非法则返回原因，合法返回空串。

    两层防御：

    1. 字符校验——拒绝非字符串/空串、超长、``/``、``\\``、null 字节、
       整值 ``.``/``..``（不拒绝 ``study..2024`` 这类含子串的合法名）、
       Windows 盘符模式（``C:`` 开头，含盘符相对路径 ``C:evil``）；
    2. containment 双保险——root 非 None 时要求 ``(root / value).resolve()``
       仍落在 ``root.resolve()`` 之内（防项目目录内预置符号链接逃逸）。
    """
    if not isinstance(value, str) or not value:
        return f"{field} 须为非空字符串"
    if len(value) > max_len:
        return f"{field} 长度不得超过 {max_len}"
    if "/" in value or "\\" in value:
        return f"{field} 不得含路径分隔符（/ 或 \\）"
    if "\x00" in value:
        return f"{field} 不得含 null 字节"
    if value == "." or value == "..":
        return f"{field} 不得为相对路径段（. 或 ..）"
    if _DRIVE_LETTER_RE.match(value):
        return f"{field} 不得为 Windows 盘符路径"
    if root is not None:
        try:
            if not (root / value).resolve().is_relative_to(root.resolve()):
                return f"{field} 不得指向根目录之外"
        except (ValueError, OSError):
            return f"{field} 含无法解析的路径成分"
    return ""


def project_id_violation(project_id: Any, repo: Any = None) -> str:
    """project_id 专用：repo 带 root 属性（JsonRepository 的 property）则做 containment。"""
    return path_id_violation(
        project_id, field="project_id", root=getattr(repo, "root", None)
    )


def turn_id_violation(turn_id: Any) -> str:
    """turn_id 专用：16 位小写十六进制（服务端 secrets.token_hex(8) 生成格式）。"""
    if not isinstance(turn_id, str) or not TURN_ID_RE.fullmatch(turn_id):
        return "turn_id 须为 16 位小写十六进制（服务端生成格式）"
    return ""

CACHE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def cache_id_violation(cache_id: Any) -> str:
    """搜索快照 id 专用（审计二 R01）：单段白名单，先于任何文件系统拼接。"""
    if not isinstance(cache_id, str) or not CACHE_ID_RE.fullmatch(cache_id):
        return "cid 须为 1-64 位字母数字、下划线或连字符"
    return ""

OWNER_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}:[^\s:]{1,127}$")


def owner_violation(owner: str | None) -> str:
    """归属标识形状校验（只管形状不管语义）：``<命名空间>:<标识>``；None=无主合法。"""
    if owner is None:
        return ""
    if OWNER_RE.fullmatch(owner):
        return ""
    return ("owner 须形如 <命名空间>:<标识>"
            "（命名空间 ^[a-z][a-z0-9_]{0,31}$，标识非空且不含空白与冒号）")
