# web_host/api/projects.py
"""项目 CRUD 路由（spec §3.2）。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fieldwork_agent.kernel.repository.guard import cache_id_violation
from fieldwork_agent.web_host.api.guard import checked_project_id
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
    pid = body.project_id or f"field_{datetime.now():%Y%m%d_%H%M%S}"
    checked_project_id(pid, repo)   # 审计 F01：守卫先于一切副作用
    repo.create(pid)
    patch = {k: v for k, v in {"discipline": body.discipline,
                                "topic": body.topic, "purpose": body.purpose}.items() if v}
    if patch:
        repo.update_research_context(pid, patch)
    return {"project_id": pid, "phase": repo.get_phase(pid)}


@router.get("/projects")
async def list_projects(repo=Depends(get_repo)):
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
    checked_project_id(project_id, repo)
    try:
        state = repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    return {"project_id": project_id, "phase": state["phase"],
            "research_context": repo.get_research_context(project_id),
            "field_sites": repo.list_field_sites(project_id),
            "informants": repo.list_informants(project_id),
            "proposals": [{"id": p["id"], "kind": p.get("kind"),
                           "version": p.get("version"), "created_at": p.get("saved_at")}
                          for p in repo.list_proposals(project_id)],
            "phase_history": state.get("phase_history", []),
            "updated_at": state.get("updated_at"),
            "proposal_confirmed": bool(state.get("proposal_confirmed")),
            "proposal_confirmation_stale": repo.is_proposal_confirmation_stale(project_id)}


@router.get("/projects/{project_id}/cached-searches")
async def list_cached_searches(project_id: str, repo=Depends(get_repo)):
    """快照索引（spec §5.2）：[{id, query, tool, cached_at}]，不含 result 体。"""
    checked_project_id(project_id, repo)
    return repo.list_cached_searches(project_id)


@router.get("/projects/{project_id}/cached-searches/{cid}")
async def get_cached_search(project_id: str, cid: str, repo=Depends(get_repo)):
    """单条快照全量（含 result）。

    审计二 R01 分层：cid 白名单不过 → 400；合法但不在本项目 → 404。
    """
    checked_project_id(project_id, repo)
    if cache_id_violation(cid):
        raise HTTPException(400, "cid 格式非法：1-64 位字母数字、下划线或连字符")
    record = repo.get_cached_search(project_id, cid)
    if record is None:
        raise HTTPException(404, f"快照不存在: {cid}")
    return record


@router.get("/projects/{project_id}/knowledge")
async def list_knowledge(project_id: str, repo=Depends(get_repo)):
    """项目知识条目（按创建顺序，含正文）。

    审计三 Fix 1b：agent add_knowledge_entry 落盘材料的 REST 可见面——
    症状「声称已存入知识库但 Web 上看不到」的服务端兑底。
    """
    checked_project_id(project_id, repo)
    try:
        return repo.list_knowledge(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
