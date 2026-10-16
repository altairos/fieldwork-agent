# web_host/api/state.py
"""项目状态修正与确认路由（审计第二批 F03/F04）：研究上下文修正、
田野点确认、报道人采纳、方案确认。守卫先于一切副作用；每次写后跑
确定性阶段推进，把 phase_transition 原样返回给前端。"""
from fastapi import APIRouter, Depends, HTTPException
from fieldwork_agent.kernel.context_patch import sanitize_context_patch
from fieldwork_agent.kernel.documents.informants import candidate_to_record
from fieldwork_agent.kernel.models import InformantCandidate
from fieldwork_agent.web_host.agent.loop import check_and_advance
from fieldwork_agent.web_host.api.guard import checked_project_id
from fieldwork_agent.web_host.deps import get_repo
from pydantic import BaseModel, ConfigDict, ValidationError

router = APIRouter(tags=["state"])


class ContextPatch(BaseModel):
    """审计二 R05：strict + extra=forbid——类型不符/白名单外键交 Pydantic 折 422。"""

    model_config = ConfigDict(strict=True, extra="forbid")

    discipline: str | None = None
    topic: str | None = None
    site_status: str | None = None
    scale: str | None = None
    budget: str | None = None
    time_window: str | None = None
    departure_city: str | None = None
    target_location: str | None = None
    travel_date: str | None = None
    purpose: str | None = None
    interviews_not_applicable: bool | None = None
    interviews_na_reason: str | None = None


class AdoptInformantRequest(BaseModel):
    candidate: dict


def _guarded_state(repo, project_id: str) -> None:
    checked_project_id(project_id, repo)
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None


def _transition(repo, project_id: str):
    t = check_and_advance(repo, project_id)
    return {"from": t["from"], "to": t["to"]} if t else None


@router.put("/projects/{project_id}/research-context")
async def update_research_context(project_id: str, body: ContextPatch,
                                  repo=Depends(get_repo)):
    _guarded_state(repo, project_id)
    try:
        # 审计二 R05：与工具入口同一校验来源；repo 层浅合并，只传净化后 patch
        clean = sanitize_context_patch(
            body.model_dump(exclude_unset=True),
            current=repo.get_research_context(project_id))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    repo.update_research_context(project_id, clean)
    return {"research_context": repo.get_research_context(project_id),
            "phase_transition": _transition(repo, project_id)}


@router.post("/projects/{project_id}/field-sites/{site_id}/confirm")
async def confirm_field_site(project_id: str, site_id: str, repo=Depends(get_repo)):
    _guarded_state(repo, project_id)
    if not repo.confirm_field_site(project_id, site_id):
        raise HTTPException(404, f"田野点不存在: {site_id}")
    record = next((s for s in repo.list_field_sites(project_id)
                   if s.get("id") == site_id), {})
    return {"field_site": record,
            "phase_transition": _transition(repo, project_id)}


@router.post("/projects/{project_id}/informants", status_code=201)
async def adopt_informant(project_id: str, body: AdoptInformantRequest,
                          repo=Depends(get_repo)):
    _guarded_state(repo, project_id)
    try:
        candidate = InformantCandidate.model_validate(body.candidate)
    except ValidationError as exc:
        raise HTTPException(400, f"候选档案不合法: {exc.errors()[0]['msg']}") from None
    seq = len(repo.list_informants(project_id)) + 1
    record = candidate_to_record(candidate, seq)
    rid = repo.save_informant(project_id, record)
    return {"informant": {**record, "id": rid},
            "phase_transition": _transition(repo, project_id)}


@router.post("/projects/{project_id}/proposal/confirm")
async def confirm_proposal(project_id: str, repo=Depends(get_repo)):
    _guarded_state(repo, project_id)
    if not repo.list_proposals(project_id):
        raise HTTPException(400, "尚无方案文档：先生成方案再确认")
    confirmation = repo.confirm_proposal(project_id)
    return {"confirmation": confirmation, "proposal_confirmed": True,
            "phase_transition": _transition(repo, project_id)}
