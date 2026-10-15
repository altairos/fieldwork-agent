"""9 个状态工具：Server 组合 repository + guidance（spec §2 前言的宿主接线权）。

handler 统一收具名参数（server 以 ``handlers[name](**arguments)`` 调用，控制者裁决 1），
返回统一包络 dict；凡收 ``project_id`` 的 handler 外层兜 ``KeyError``（Protocol 契约：
未知 project_id 一律 KeyError）折成 ``project_not_found`` 错误包络（裁决 2），
不崩溃、不向 MCP 协议层泄业务异常。``ValueError``（如非法阶段）不属兜底范围，自然抛出。

终审 C1：``project_id`` 是客户端可控输入，而 ``JsonRepository`` 以 ``root / pid``
直拼文件系统路径——本模块提供统一净化守卫 ``guard_project_id``（handler 与
``resources.py`` 项目类 URI 路由两处入口共用），拒绝目录穿越（``../``、反斜杠、
Windows 绝对路径/盘符整体替换基路径）。kernel 不改动，薄壳层拦截。
"""
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.kernel.models import FieldSiteRecord, InformantRecord, ResearchContext
from fieldwork_agent.kernel.repository.protocol import ProjectRepository
from mcp.types import Tool, ToolAnnotations

ITINERARY_STATE_KEYS = ("itinerary", "travel_plan")   # 兼容旧键

# project_id 净化规则（终审 C1）：字符黑名单 + containment 双保险
PID_MAX_LEN = 64
PID_RULE_HINT = "project_id 仅允许字母数字下划线连字符等安全字符"
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
_PATH_SEP_RE = re.compile(r"[\\/]")   # 段级 .. 判定用（终审 L）


def _ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def _err(code: str, message: str, hint: str = "") -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def _not_found(project_id: str) -> dict:
    """裁决 2 的统一兜底形状：未知 project_id → project_not_found 包络。"""
    return _err("project_not_found", f"项目不存在: {project_id}",
                "先 create_project 或使用 list_projects 查已有项目")


def _pid_violation(project_id: Any, repo: Any = None) -> str:
    """project_id 非法则返回原因，合法返回空串。

    两层防御：

    1. 字符校验——拒绝非字符串/空串、长度 >64、``/``、``\\``、``..`` 路径段、null 字节、
       Windows 盘符模式（``C:`` 开头，含盘符相对路径 ``C:evil``）；
    2. containment 双保险——repo 若带 ``root`` 属性（``JsonRepository`` 的非
       Protocol 成员，json_repo.py 明示"仅供宿主排障与契约套件直读"，薄壳属宿主），
       则要求 ``(root / pid).resolve()`` 仍落在 ``root.resolve()`` 之内；
       仅实现 ``ProjectRepository`` Protocol 的替身没有 ``root``，只靠字符校验。
    """
    if not isinstance(project_id, str) or not project_id:
        return "project_id 须为非空字符串"
    if len(project_id) > PID_MAX_LEN:
        return f"project_id 长度不得超过 {PID_MAX_LEN}"
    if "/" in project_id or "\\" in project_id:
        return "project_id 不得含路径分隔符（/ 或 \\）"
    # 终审 L：按路径段判定而非子串——``".." in pid`` 会误拒 study..2024 这类合法 id。
    # 上一行已拒全部分隔符，此处仍逐段切分：规则不依赖前后顺序，单看也自洽。
    if any(seg == ".." for seg in _PATH_SEP_RE.split(project_id)):
        return "project_id 不得含 .. 路径段"
    if "\x00" in project_id:
        return "project_id 不得含 null 字节"
    if _DRIVE_LETTER_RE.match(project_id):
        return "project_id 不得为 Windows 盘符路径"
    root = getattr(repo, "root", None)
    if root is not None:
        if not (root / project_id).resolve().is_relative_to(root.resolve()):
            return "project_id 不得指向项目根目录之外"
    return ""


def guard_project_id(project_id: Any, repo: Any = None) -> dict | None:
    """统一净化守卫：非法 project_id → ``invalid_args`` 错误包络；合法 → None。

    业务失败折包络、不抛协议异常（全局约束）。``resources.read_resource`` 的
    项目类 URI 路由复用本函数（单向 import，state_tools 不依赖 resources）。
    """
    reason = _pid_violation(project_id, repo)
    if not reason:
        return None
    return _err("invalid_args", f"非法 project_id: {project_id!r}（{reason}）",
                PID_RULE_HINT)


def build_handlers(repo: ProjectRepository) -> dict[str, Callable[..., dict]]:
    """按注入的 repository 组装 9 个状态工具 handler（键 = 工具名，值 = 收具名参数的函数）。"""

    def create_project(project_id: str | None = None) -> dict:
        pid = project_id or f"field_{datetime.now():%Y%m%d_%H%M%S}"
        # 自动生成 id（field_<时间戳>）天然合法；统一走守卫更简单（终审 C1 修法 4）
        if (bad := guard_project_id(pid, repo)) is not None:
            return bad
        repo.create(pid)   # 幂等：已存在则保留存量
        return _ok({"project_id": pid})

    def list_projects() -> dict:
        return _ok(repo.list_projects())

    def get_project(project_id: str) -> dict:
        if (bad := guard_project_id(project_id, repo)) is not None:
            return bad
        try:
            return _ok(repo.get_state(project_id))
        except KeyError:
            return _not_found(project_id)

    def update_research_context(project_id: str, patch: dict) -> dict:
        if (bad := guard_project_id(project_id, repo)) is not None:
            return bad
        try:
            repo.update_research_context(project_id, patch)
            return _ok(repo.get_research_context(project_id))
        except KeyError:
            return _not_found(project_id)

    def save_field_site(project_id: str, record: dict) -> dict:
        if (bad := guard_project_id(project_id, repo)) is not None:
            return bad
        try:
            return _ok({"id": repo.save_field_site(project_id, record)})
        except KeyError:
            return _not_found(project_id)

    def save_informant(project_id: str, record: dict) -> dict:
        if (bad := guard_project_id(project_id, repo)) is not None:
            return bad
        try:
            return _ok({"id": repo.save_informant(project_id, record)})
        except KeyError:
            return _not_found(project_id)

    def add_knowledge_entry(project_id: str, title: str, content: str,
                            source: str = "user", tags: list[str] | None = None,
                            category: str = "general") -> dict:
        if (bad := guard_project_id(project_id, repo)) is not None:
            return bad
        try:
            return _ok({"id": repo.add_knowledge(project_id, title, content,
                                                 source=source, tags=tags,
                                                 category=category)})
        except KeyError:
            return _not_found(project_id)

    def search_knowledge(project_id: str, text: str) -> dict:
        if (bad := guard_project_id(project_id, repo)) is not None:
            return bad
        try:
            return _ok(repo.search_knowledge(project_id, text))
        except KeyError:
            return _not_found(project_id)

    def check_phase_advance(project_id: str) -> dict:
        if (bad := guard_project_id(project_id, repo)) is not None:
            return bad
        try:
            state = repo.get_state(project_id)
            ctx = ResearchContext.model_validate(repo.get_research_context(project_id))
            sites = [FieldSiteRecord.model_validate(s)
                     for s in repo.list_field_sites(project_id)]
            informants = [InformantRecord.model_validate(i)
                          for i in repo.list_informants(project_id)]
        except KeyError:
            return _not_found(project_id)
        has_itinerary = any(state.get(k) for k in ITINERARY_STATE_KEYS)
        # 确定性判定（纯规则，绝不经 LLM）；实际切阶段由宿主 set_phase 落地
        result = phases.check_advance(
            state["phase"], ctx, sites, informants,
            has_itinerary, bool(state.get("proposals")),
            bool(state.get("proposal_confirmed")))
        return _ok(result.model_dump())

    return {"create_project": create_project, "list_projects": list_projects,
            "get_project": get_project, "update_research_context": update_research_context,
            "save_field_site": save_field_site, "save_informant": save_informant,
            "add_knowledge_entry": add_knowledge_entry,
            "search_knowledge": search_knowledge,
            "check_phase_advance": check_phase_advance}


# ---------------------------------------------------------------- Tool 定义（手写 9 个）
# annotations 一律 readOnlyHint=False, destructiveHint=False——upsert 幂等语义由 Protocol 保证。

def _annotations() -> ToolAnnotations:
    return ToolAnnotations(readOnlyHint=False, destructiveHint=False)


_PID = {"type": "string", "description": "项目唯一标识；未知 project_id 返回 project_not_found"}

STATE_TOOLS: list[Tool] = [
    Tool(name="create_project",
         description="创建田野项目（幂等：同 project_id 重复调用保留存量数据）。"
                     "project_id 缺省时自动生成 field_<时间戳>；新项目从 research_positioning 阶段开始。",
         inputSchema={"type": "object",
                      "properties": {
                          "project_id": {"type": "string",
                                         "description": "项目唯一标识；缺省自动生成 field_<时间戳>"}}},
         annotations=_annotations()),
    Tool(name="list_projects",
         description="列出全部项目摘要（project_id/phase/created_at/updated_at）；无需参数。",
         inputSchema={"type": "object", "properties": {}},
         annotations=_annotations()),
    Tool(name="get_project",
         description="读取整份项目状态（副本，含 phase/research_context/field_sites/informants/proposals 等）。",
         inputSchema={"type": "object", "properties": {"project_id": _PID},
                      "required": ["project_id"]},
         annotations=_annotations()),
    Tool(name="update_research_context",
         description="按 patch 浅合并更新研究上下文（只覆盖 patch 中出现的键），返回更新后的完整上下文。",
         inputSchema={"type": "object",
                      "properties": {"project_id": _PID,
                                     "patch": {"type": "object",
                                               "description": "要合并的键值对（如 discipline/topic/scale/budget）"}},
                      "required": ["project_id", "patch"]},
         annotations=_annotations()),
    Tool(name="save_field_site",
         description="保存田野点记录（record 带 id 则更新，否则新建；upsert 幂等），返回记录 id。",
         inputSchema={"type": "object",
                      "properties": {"project_id": _PID,
                                     "record": {"type": "object",
                                                "description": "田野点记录（name/confirmed/profile 等；带 id 则更新）"}},
                      "required": ["project_id", "record"]},
         annotations=_annotations()),
    Tool(name="save_informant",
         description="保存报道人（访谈对象）记录（record 带 id 则更新，否则新建；upsert 幂等），返回记录 id。",
         inputSchema={"type": "object",
                      "properties": {"project_id": _PID,
                                     "record": {"type": "object",
                                                "description": "报道人记录（code/type/relation/contact/notes；带 id 则更新）"}},
                      "required": ["project_id", "record"]},
         annotations=_annotations()),
    Tool(name="add_knowledge_entry",
         description="向项目知识库新增一条知识条目，返回条目 id；随后可用 search_knowledge 检索。",
         inputSchema={"type": "object",
                      "properties": {"project_id": _PID,
                                     "title": {"type": "string", "description": "条目标题"},
                                     "content": {"type": "string", "description": "条目正文"},
                                     "source": {"type": "string", "description": "来源标注，缺省 user"},
                                     "tags": {"type": "array", "items": {"type": "string"},
                                              "description": "标签列表"},
                                     "category": {"type": "string", "description": "分类，缺省 general"}},
                      "required": ["project_id", "title", "content"]},
         annotations=_annotations()),
    Tool(name="search_knowledge",
         description="关键词过滤检索项目知识库（标题+正文+标签，大小写不敏感，空白分词 AND 匹配）。",
         inputSchema={"type": "object",
                      "properties": {"project_id": _PID,
                                     "text": {"type": "string",
                                              "description": "检索文本（空白分词，要求全部命中）"}},
                      "required": ["project_id", "text"]},
         annotations=_annotations()),
    Tool(name="check_phase_advance",
         description="确定性判定项目当前阶段能否推进（纯规则，不经 LLM），返回 can_advance/to_phase/"
                     "missing_fields；实际阶段切换由宿主落地，本工具只读不改状态。",
         inputSchema={"type": "object", "properties": {"project_id": _PID},
                      "required": ["project_id"]},
         annotations=_annotations()),
]
