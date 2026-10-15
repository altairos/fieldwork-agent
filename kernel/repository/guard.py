"""``project_id`` 穿越守卫 — 持久化边界的规则单一来源（终审 C1）。

``JsonRepository`` 以 ``root / pid`` 直拼文件系统路径，而 ``project_id`` 是客户端
可控输入（MCP 工具参数、HTTP path/body 参数），非法值可越出 projects 根创建/读写
任意目录。两个宿主（mcp_server / web_host）的薄壳在触达 repository/TurnManager
之前**必须**先过本模块守卫：规则语义只在此处定义一份（单一来源，杜绝复制漂移），
宿主侧只负责把拒绝原因折进各自协议包络（MCP ``invalid_args`` dict / HTTP 400）。

本模块只用标准库（``re``/``pathlib``/``typing``），不 import 宿主或 kernel 其他
子包——``kernel.repository`` 是持久化边界的稳定层，project_id 的语法与 containment
策略属于该边界的契约；宿主之间不得互相依赖（spec §0/§1 共享内核、双宿主薄壳），
故 web_host 不许 import mcp_server.state_tools，两边共同依赖这里。

前提钉住：本守卫的入参必须是**已解码**的值。MCP 链路全程无 URL 解码，字面量
``..%2f`` 能通过字符校验（无害：containment 双保险兜底）；HTTP 链路 Starlette 在
进 handler 前已解码 path 参数（``..%2F`` 到手已是 ``../``），恰好满足本前提。
将来任何新宿主从 URL/query 取 project_id，解码必须发生在守卫之前，否则该字面量
解码后即成真实穿越段。
"""

import re
from pathlib import Path
from typing import Any

# project_id 净化规则（终审 C1）：字符黑名单 + containment 双保险。
# 常量以本模块为单一来源，宿主薄壳可 re-export 但不得另立第二份。
PID_MAX_LEN = 64
PID_RULE_HINT = "project_id 仅允许字母数字下划线连字符等安全字符"
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
_PATH_SEP_RE = re.compile(r"[\\/]")   # 段级 .. 判定用（终审 L）


def project_id_violation(project_id: Any, root: Path | None = None) -> str:
    """project_id 非法则返回中文原因，合法返回空串（纯函数，不做任何 I/O）。

    两层防御：

    1. 字符校验——拒绝非字符串/空串、长度 >64、``/``、``\\``、``..`` 路径段、null 字节、
       Windows 盘符模式（``C:`` 开头，含盘符相对路径 ``C:evil``）；
    2. containment 双保险——给了 ``root``（宿主薄壳以 ``getattr(repo, "root", None)``
       探测；``root`` 是 ``JsonRepository`` 的非 Protocol 成员，json_repo.py 明示
       "仅供宿主排障与契约套件直读"，薄壳属宿主）则要求 ``(root / pid).resolve()``
       仍落在 ``root.resolve()`` 之内；仅实现 ``ProjectRepository`` Protocol 的替身
       没有 ``root``，传 None 即跳过本层，只靠字符校验。
    """
    if not isinstance(project_id, str) or not project_id:
        return "project_id 须为非空字符串"
    if len(project_id) > PID_MAX_LEN:
        return f"project_id 长度不得超过 {PID_MAX_LEN}"
    if "/" in project_id or "\\" in project_id:
        return "project_id 不得含路径分隔符（/ 或 \\）"
    # 终审 L：按路径段判定而非子串——``".." in pid`` 会误拒 study..2024 这类合法 id。
    # 上一行已拒全部分隔符，此处仍逐段切分：规则不依赖前后顺序，单看也自洽。
    # （"入参必须已解码"的前提说明见模块 docstring。）
    # 段级三拒：".."（穿越）、"."（root/"." 解析回 projects 根本身，会把 state.json 与
    # knowledge/search_cache/output 直接写进根目录，污染根并干扰 list_projects）、
    # 空段（"a//b" 类退化形；上方分隔符规则已先拦，此处纵深防御不依赖规则顺序）。
    if any(seg in ("", ".", "..") for seg in _PATH_SEP_RE.split(project_id)):
        return "project_id 不得含空段、. 或 .. 路径段"
    if "\x00" in project_id:
        return "project_id 不得含 null 字节"
    if _DRIVE_LETTER_RE.match(project_id):
        return "project_id 不得为 Windows 盘符路径"
    if root is not None:
        if not (root / project_id).resolve().is_relative_to(root.resolve()):
            return "project_id 不得指向项目根目录之外"
    return ""
