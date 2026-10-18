"""路径 ID 守卫（审计 F01）：project_id / turn_id 校验的单一实现来源。

mcp_server（折 tool 错误包络）与 web_host（折 HTTPException）两个外壳薄壳
复用，规则变更只改本文件。kernel 只依赖标准库。
"""

import re
import secrets
from datetime import datetime
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
# X-Owner-Token 裸值形状（客户端令牌）：归一化前的准入校验，与 OWNER_RE
# （归一化后的存储值形状）分工——前者管客户端输入，后者管落库值。
OWNER_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def owner_violation(owner: str | None) -> str:
    """归属标识形状校验（只管形状不管语义）：``<命名空间>:<标识>``；None=无主合法。"""
    if owner is None:
        return ""
    if OWNER_RE.fullmatch(owner):
        return ""
    return ("owner 须形如 <命名空间>:<标识>"
            "（命名空间 ^[a-z][a-z0-9_]{0,31}$，标识非空且不含空白与冒号）")


def normalize_owner_token(raw: str | None) -> str | None:
    """X-Owner-Token 裸值 → 归一化归属值 ``anon:<raw>``（身份映射单一来源）。

    REST（web_host.api.owner）与 MCP HTTP（mcp_server.http_entry）两入口共用，
    ``anon:`` 前缀只由服务端拼接、客户端永不携带：

    - 空 / 纯空白 → None（无令牌；后续行为由各入口端点/handler 矩阵决定）；
    - 形状非法 → ``ValueError``（调用方折 400，或按无令牌降级为拒绝访问）；
    - 合法 → ``anon:<raw>``。

    未来接入账号体系时，本函数与 ``owner_violation`` 是唯一的身份形状来源。
    """
    token = (raw or "").strip()
    if not token:
        return None
    if not OWNER_TOKEN_RE.fullmatch(token):
        raise ValueError("非法 X-Owner-Token 头")
    return f"anon:{token}"


def new_project_id() -> str:
    """自动生成项目 ID：``field_<时间戳>_<随机>``（N02：随机段消除同秒碰撞）。

    时间戳保可读性与大致时序；6 位十六进制随机段（secrets）消除不同用户
    同秒创建的碰撞面（旧实现仅精确到秒）；形状恒过 project_id_violation
    （字母数字下划线，长度远低于 PID_MAX_LEN）。REST 与 MCP 两入口共用。
    """
    return f"field_{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(3)}"
