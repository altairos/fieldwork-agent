# web_host/api/projects.py
"""项目 CRUD 路由（spec §3.2）。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fieldwork_agent.kernel.repository.guard import cache_id_violation
from fieldwork_agent.web_host.api.guard import checked_owned_project, checked_project_id
from fieldwork_agent.web_host.api.owner import owner_from_request
from fieldwork_agent.web_host.deps import get_repo, get_turn_manager
from pydantic import BaseModel

router = APIRouter(tags=["projects"])


class CreateProjectRequest(BaseModel):
    project_id: str | None = None
    discipline: str = ""
    topic: str = ""
    purpose: str = ""


@router.post("/projects", status_code=201)
async def create_project(body: CreateProjectRequest, repo=Depends(get_repo),
                         owner: str | None = Depends(owner_from_request)):
    if owner is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "owner_token_required",
                    "message": "创建项目需携带 X-Owner-Token 头"},
        )
    pid = body.project_id or f"field_{datetime.now():%Y%m%d_%H%M%S}"
    checked_project_id(pid, repo)   # 审计 F01：守卫先于一切副作用
    repo.create(pid, owner=owner)
    patch = {k: v for k, v in {"discipline": body.discipline,
                                "topic": body.topic, "purpose": body.purpose}.items() if v}
    if patch:
        repo.update_research_context(pid, patch)
    return {"project_id": pid, "phase": repo.get_phase(pid)}


@router.get("/projects")
async def list_projects(repo=Depends(get_repo),
                        owner: str | None = Depends(owner_from_request)):
    if owner is None:
        return []
    results = []
    for summary in repo.list_projects(owner=owner):
        pid = summary["project_id"]
        ctx = repo.get_research_context(pid)
        results.append({**summary, "research_context": ctx,
                        "counts": {"sites": len(repo.list_field_sites(pid)),
                                   "informants": len(repo.list_informants(pid)),
                                   "proposals": len(repo.list_proposals(pid))}})
    return results


@router.get("/projects/{project_id}")
async def get_project(project_id: str, repo=Depends(get_repo),
                      owner: str | None = Depends(owner_from_request)):
    state = checked_owned_project(project_id, repo, owner)
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


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request, repo=Depends(get_repo),
                         owner: str | None = Depends(owner_from_request)):
    """删除即整域清除：取消活动回合 → repo 数据 → turns 注册表与磁盘残留。"""
    checked_owned_project(project_id, repo, owner)
    tm = get_turn_manager(request)
    tm.cancel_all(project_id)
    repo.delete_project(project_id)
    tm.purge_project(project_id)
    return Response(status_code=204)


@router.get("/projects/{project_id}/cached-searches")
async def list_cached_searches(project_id: str, repo=Depends(get_repo),
                               owner: str | None = Depends(owner_from_request)):
    """快照索引（spec §5.2 + C5/R3）：[{id,query,tool,cached_at,parent_call_id,turn_id,query_key,empty,truncated,total}]，不含 result 体；empty/truncated/total 为 null 表示存量旧记录 unknown。"""
    checked_owned_project(project_id, repo, owner)
    return repo.list_cached_searches(project_id)


@router.get("/projects/{project_id}/cached-searches/{cid}")
async def get_cached_search(project_id: str, cid: str, repo=Depends(get_repo),
                            owner: str | None = Depends(owner_from_request)):
    """单条快照全量（含 result）。

    审计二 R01 分层：cid 白名单不过 → 400；合法但不在本项目 → 404。
    """
    checked_owned_project(project_id, repo, owner)
    if cache_id_violation(cid):
        raise HTTPException(400, "cid 格式非法：1-64 位字母数字、下划线或连字符")
    record = repo.get_cached_search(project_id, cid)
    if record is None:
        raise HTTPException(404, f"快照不存在: {cid}")
    return record


@router.get("/projects/{project_id}/knowledge")
async def list_knowledge(project_id: str, repo=Depends(get_repo),
                         owner: str | None = Depends(owner_from_request)):
    """项目知识条目（按创建顺序，含正文）。

    审计三 Fix 1b：agent add_knowledge_entry 落盘材料的 REST 可见面——
    症状「声称已存入知识库但 Web 上看不到」的服务端兑底。
    """
    checked_owned_project(project_id, repo, owner)
    return repo.list_knowledge(project_id)


class UpdateKnowledgeRequest(BaseModel):
    title: str | None = None
    content: str | None = None


@router.put("/projects/{project_id}/knowledge/{entry_id}")
async def update_knowledge(
    project_id: str, entry_id: str, body: UpdateKnowledgeRequest, repo=Depends(get_repo),
    owner: str | None = Depends(owner_from_request),
):
    """用户编辑知识条目（P1 右栏折叠式编辑）：未提供字段不改，返回更新后条目。"""
    checked_owned_project(project_id, repo, owner)
    try:
        return repo.update_knowledge(
            project_id, entry_id, title=body.title, content=body.content)
    except KeyError:
        raise HTTPException(404, f"知识条目不存在: {entry_id}") from None
    except ValueError:
        # json 实现的 path_id_violation 守卫（同 delete 语义）→ 400
        raise HTTPException(400, "entry_id 格式非法") from None
