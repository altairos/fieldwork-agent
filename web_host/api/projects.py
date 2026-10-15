# web_host/api/projects.py
"""项目 CRUD 路由（spec §3.2）。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fieldwork_agent.web_host.api.guard import checked_pid
from fieldwork_agent.web_host.deps import get_repo
from pydantic import BaseModel

router = APIRouter(tags=["projects"])


class CreateProjectRequest(BaseModel):
    project_id: str | None = None
    discipline: str = ""
    topic: str = ""
    purpose: str = ""


@router.post("/projects", status_code=201)
async def create_project(body: CreateProjectRequest, repo=Depends(get_repo)):
    # 终审 C1（HTTP 侧）：body.project_id 客户端可控且 repo.create 以 root/pid 直拼路径，
    # 统一对最终 pid 过守卫（自动生成的 field_<时间戳> 天然合法，也走同一接入点）；
    # 必须先于 repo.create，否则穿越 id 可在 projects 根外任意建目录并写 state.json
    pid = checked_pid(body.project_id or f"field_{datetime.now():%Y%m%d_%H%M%S}", repo)
    repo.create(pid)
    patch = {k: v for k, v in {"discipline": body.discipline,
                                "topic": body.topic, "purpose": body.purpose}.items() if v}
    if patch:
        repo.update_research_context(pid, patch)
    return {"project_id": pid, "phase": repo.get_phase(pid)}


@router.get("/projects")
async def list_projects(repo=Depends(get_repo)):
    # pid 取自 repo.list_projects()（磁盘目录名，非客户端可控），无需守卫
    results = []
    for summary in repo.list_projects():
        pid = summary["project_id"]
        ctx = repo.get_research_context(pid)
        results.append({**summary, "research_context": ctx,
                        "counts": {"sites": len(repo.list_field_sites(pid)),
                                   "informants": len(repo.list_informants(pid)),
                                   "proposals": len(repo.list_proposals(pid))}})
    return results


@router.get("/projects/{project_id}")
async def get_project(project_id: str, repo=Depends(get_repo)):
    # Starlette 在进 handler 前已解码 path 参数（..%2F 到手已是 ../），满足守卫前提；
    # 非法 → 400，先于 repo.get_state（否则穿越 id 可越根读状态）
    project_id = checked_pid(project_id, repo)
    try:
        state = repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    return {"project_id": project_id, "phase": state["phase"],
            "research_context": repo.get_research_context(project_id),
            "field_sites": repo.list_field_sites(project_id),
            "informants": repo.list_informants(project_id),
            "proposals": [{"id": p["id"], "kind": p.get("kind"),
                           "version": p.get("version"), "created_at": p.get("created_at")}
                          for p in repo.list_proposals(project_id)],
            "phase_history": state.get("phase_history", []),
            "updated_at": state.get("updated_at")}
