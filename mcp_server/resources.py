"""MCP Resources — 10 类资源随协议分发（spec §2.2）。

静态资源从 kernel 常量拼装（禁止复制第二份事实），项目类资源按 repo 存量展开。
URI 路由：前缀匹配 → 渲染函数；未知 URI 返回 ok:false 包络，不抛裸异常。
终审 C1：项目类 URI 解析出的 pid 先过 ``state_tools.guard_project_id`` 同一净化
守卫（``split("/")`` 对反斜杠路径失效），非法 → ``invalid_args`` 包络再谈路由。
"""

import inspect
from typing import Any

from fieldwork_agent.kernel.documents.proposal import PROPOSAL_SECTIONS
from fieldwork_agent.kernel.guidance import informants as informant_lib
from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.kernel.repository.protocol import ProjectRepository
from fieldwork_agent.mcp_server.state_tools import guard_project_id
from mcp.types import Resource

# ---------------------------------------------------------------- 渲染辅助

def _render_phases() -> str:
    """渲染 guidance/phases 资源：阶段列表 + 各阶段必填字段 + 推进契约。"""
    lines: list[str] = ["# 田野调查阶段与推进条件", ""]
    for i, p in enumerate(phases.PHASES, 1):
        fields = phases.REQUIRED_FIELDS.get(p, [])
        lines.append(f"## {i}. {p}")
        if fields:
            lines.append(f"必填字段：{', '.join(fields)}")
        else:
            lines.append("必填字段：（无）")
        lines.append("")
    # 三态契约文字——从 check_advance docstring 动态提取
    doc = inspect.getdoc(phases.check_advance) or ""
    lines.append("## 推进判定契约（check_advance）")
    lines.append("")
    lines.append(doc)
    return "\n".join(lines)


def _render_questions(phase: str) -> str:
    """渲染 guidance/questions/{phase} 资源。"""
    qs = phases.guiding_questions(phase)
    lines = [f"# 引导问题：{phase}", ""]
    for i, q in enumerate(qs, 1):
        lines.append(f"{i}. {q}")
    return "\n".join(lines)


def _render_checklist(phase: str) -> str:
    """渲染 guidance/checklists/{phase} 资源。"""
    fields = phases.REQUIRED_FIELDS.get(phase, [])
    lines = [f"# 采集清单：{phase}", ""]
    if fields:
        for f in fields:
            lines.append(f"- [ ] {f}")
    else:
        lines.append("（本阶段无必填字段）")
    return "\n".join(lines)


def _render_informants_library() -> str:
    """渲染 guidance/informants/library：全量示范词条 markdown。"""
    lines = [
        "# 访谈对象类型库（示范词条）",
        "",
        "> 以下为参考起点，非枚举白名单。实际访谈对象应根据研究主题动态生成。",
        "",
    ]
    for key, archive in informant_lib.INFORMANT_EXAMPLES.items():
        lines.append(f"## {key}")
        lines.append("")
        lines.append(f"**接近策略：** {archive['approach']}")
        lines.append(f"**注意事项：** {archive['cautions']}")
        lines.append(f"**提问域：** {', '.join(archive['question_domains'])}")
        lines.append(f"**伦理提示：** {archive['ethics']}")
        lines.append(f"**进入途径：** {archive['access_channel']}")
        lines.append(f"**行前保障：** {archive['preparation']}")
        lines.append("")
        lines.append("示例提问：")
        for ex in archive.get("examples", []):
            lines.append(f"  - {ex}")
        lines.append("")
    return "\n".join(lines)


def _render_informant_entry(informant_type: str) -> str:
    """渲染 guidance/informants/{type}：单词条完整档案。"""
    entry = informant_lib.get_informant(informant_type)
    if entry is None:
        return f"# {informant_type}\n\n（未收录）"
    lines = [
        f"# {entry.get('name', informant_type)}",
        "",
        f"**接近策略：** {entry['approach']}",
        f"**注意事项：** {entry['cautions']}",
        f"**提问域：** {', '.join(entry['question_domains'])}",
        f"**伦理提示：** {entry['ethics']}",
        f"**进入途径：** {entry['access_channel']}",
        f"**行前保障：** {entry['preparation']}",
        "",
        "示例提问：",
    ]
    for ex in entry.get("examples", []):
        lines.append(f"  - {ex}")
    return "\n".join(lines)


def _render_methodology() -> str:
    """渲染 guidance/interview-guide/methodology：原文直出。"""
    return informant_lib.INTERVIEW_GUIDE_METHODOLOGY


def _render_proposal_outline() -> str:
    """渲染 templates/proposal-outline：八章 + 各章一句写作要点。"""
    lines = ["# Research Proposal 提纲", ""]
    for section in PROPOSAL_SECTIONS:
        lines.append(f"## {section}")
        lines.append("")
        lines.append(f"（写作要点：围绕「{section.split('、')[1] if '、' in section else section}」"
                     f"展开，确保数据具体、引用可追溯。）")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 项目资源渲染

def _render_project_summary(repo: ProjectRepository, pid: str) -> str:
    """渲染 project/{id}/summary。"""
    ctx = repo.get_research_context(pid)
    state = repo.get_state(pid)
    lines = [f"# 项目摘要：{pid}", ""]
    lines.append(f"阶段：{state.get('phase', '未知')}")
    lines.append("")
    lines.append("## 研究上下文")
    for k, v in ctx.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _render_project_sites(repo: ProjectRepository, pid: str) -> str:
    """渲染 project/{id}/sites。"""
    sites = repo.list_field_sites(pid)
    lines = [f"# 田野点：{pid}", ""]
    if not sites:
        lines.append("（暂无田野点记录）")
    for s in sites:
        lines.append(f"- {s.get('name', '未命名')}"
                     f"{'（已确认）' if s.get('confirmed') else ''}")
    return "\n".join(lines)


def _render_project_informants(repo: ProjectRepository, pid: str) -> str:
    """渲染 project/{id}/informants。"""
    informants = repo.list_informants(pid)
    lines = [f"# 访谈对象：{pid}", ""]
    if not informants:
        lines.append("（暂无访谈对象记录）")
    for inf in informants:
        lines.append(f"- {inf.get('type', '未知类型')}: {inf.get('code', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 静态资源目录

def _build_static_resources() -> list[Resource]:
    """构建全部静态 Resource 条目（固定 URI 全集）。"""
    items: list[Resource] = []
    # 1. guidance/phases
    items.append(Resource(
        uri="fieldwork://guidance/phases",
        name="阶段与推进条件",
        mimeType="text/markdown",
    ))
    # 2. guidance/questions/{phase} ×5
    for p in phases.PHASES:
        items.append(Resource(
            uri=f"fieldwork://guidance/questions/{p}",
            name=f"引导问题：{p}",
            mimeType="text/markdown",
        ))
    # 3. guidance/checklists/{phase} ×5
    for p in phases.PHASES:
        items.append(Resource(
            uri=f"fieldwork://guidance/checklists/{p}",
            name=f"采集清单：{p}",
            mimeType="text/markdown",
        ))
    # 4. guidance/informants/library
    items.append(Resource(
        uri="fieldwork://guidance/informants/library",
        name="访谈对象类型库",
        mimeType="text/markdown",
    ))
    # 5. guidance/informants/{type} ×N
    for key in informant_lib.INFORMANT_EXAMPLES:
        items.append(Resource(
            uri=f"fieldwork://guidance/informants/{key}",
            name=f"访谈对象：{key}",
            mimeType="text/markdown",
        ))
    # 6. guidance/interview-guide/methodology
    items.append(Resource(
        uri="fieldwork://guidance/interview-guide/methodology",
        name="访谈方法论",
        mimeType="text/markdown",
    ))
    # 7. templates/proposal-outline
    items.append(Resource(
        uri="fieldwork://templates/proposal-outline",
        name="Proposal 提纲模板",
        mimeType="text/markdown",
    ))
    return items


STATIC_RESOURCES: list[Resource] = _build_static_resources()


# ---------------------------------------------------------------- 公开接口

def list_resources(repo: ProjectRepository) -> list[Resource]:
    """静态资源 + 按 repo 存量展开项目类三件套。"""
    items = list(STATIC_RESOURCES)
    for summary in repo.list_projects():
        pid = summary["project_id"]
        items.append(Resource(
            uri=f"fieldwork://project/{pid}/summary",
            name=f"项目摘要：{pid}",
            mimeType="text/markdown",
        ))
        items.append(Resource(
            uri=f"fieldwork://project/{pid}/sites",
            name=f"田野点：{pid}",
            mimeType="text/markdown",
        ))
        items.append(Resource(
            uri=f"fieldwork://project/{pid}/informants",
            name=f"访谈对象：{pid}",
            mimeType="text/markdown",
        ))
    return items


def _err(code: str, message: str, hint: str = "") -> dict[str, Any]:
    """错误包络（形状与 state_tools._err 一致）。"""
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def _not_found(project_id: str) -> dict[str, Any]:
    return _err("project_not_found", f"项目不存在: {project_id}",
                "先 create_project 或使用 list_projects 查已有项目")


def read_resource(repo: ProjectRepository, uri: str) -> dict[str, Any]:
    """按 URI 路由渲染资源文本；项目类 KeyError → project_not_found 包络。"""
    # 静态 guidance 路由
    if uri == "fieldwork://guidance/phases":
        return {"ok": True, "text": _render_phases()}

    if uri.startswith("fieldwork://guidance/questions/"):
        phase = uri.removeprefix("fieldwork://guidance/questions/")
        return {"ok": True, "text": _render_questions(phase)}

    if uri.startswith("fieldwork://guidance/checklists/"):
        phase = uri.removeprefix("fieldwork://guidance/checklists/")
        return {"ok": True, "text": _render_checklist(phase)}

    if uri == "fieldwork://guidance/informants/library":
        return {"ok": True, "text": _render_informants_library()}

    if uri.startswith("fieldwork://guidance/informants/"):
        informant_type = uri.removeprefix("fieldwork://guidance/informants/")
        return {"ok": True, "text": _render_informant_entry(informant_type)}

    if uri == "fieldwork://guidance/interview-guide/methodology":
        return {"ok": True, "text": _render_methodology()}

    if uri == "fieldwork://templates/proposal-outline":
        return {"ok": True, "text": _render_proposal_outline()}

    # 项目类路由
    if uri.startswith("fieldwork://project/"):
        parts = uri.removeprefix("fieldwork://project/").split("/")
        if len(parts) == 2:
            pid, kind = parts
            # 终审 C1：pid 先过与 state_tools 同一净化守卫（split("/") 挡不住反斜杠），
            # 非法 → invalid_args 包络；合法再走原 KeyError→project_not_found 逻辑
            if (bad := guard_project_id(pid, repo)) is not None:
                return bad
            try:
                if kind == "summary":
                    return {"ok": True, "text": _render_project_summary(repo, pid)}
                if kind == "sites":
                    return {"ok": True, "text": _render_project_sites(repo, pid)}
                if kind == "informants":
                    return {"ok": True, "text": _render_project_informants(repo, pid)}
            except KeyError:
                return _not_found(pid)

    # 未知 URI
    return _err("resource_not_found", f"未知资源 URI: {uri}",
                "使用 list_resources 查看可用资源")
