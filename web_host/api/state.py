# web_host/api/state.py
"""项目状态修正与确认路由（审计第二批 F03/F04）：研究上下文修正、
田野点确认、报道人采纳、方案确认。守卫先于一切副作用；每次写后跑
确定性阶段推进，把 phase_transition 原样返回给前端。"""
from fastapi import APIRouter, Depends, HTTPException
from fieldwork_agent.kernel.context_patch import (
    ALLOWED_CONTEXT_FIELDS,
    sanitize_context_patch,
)
from fieldwork_agent.kernel.documents.informants import candidate_to_record
from fieldwork_agent.kernel.models import InformantCandidate
from fieldwork_agent.kernel.repository.confirm_hash import ProposalConfirmError
from fieldwork_agent.web_host.agent.loop import check_and_advance
from fieldwork_agent.web_host.api.guard import checked_owned_project
from fieldwork_agent.web_host.api.owner import owner_from_request
from fieldwork_agent.web_host.deps import get_repo
from pydantic import BaseModel, ConfigDict, ValidationError, create_model

router = APIRouter(tags=["state"])


# N03：字段契约单一来源——ContextPatch 的字段集直接由 kernel
# ALLOWED_CONTEXT_FIELDS 生成，新增白名单字段（如 research_questions /
# candidate_windows）自动出现在 REST 入口，不再因手抄遗漏而 extra=forbid 折 422。
# 非字符串字段的具体类型在此声明（与 sanitize_context_patch 的校验对齐）。
_NON_STR_FIELD_TYPES = {
    "interviews_not_applicable": bool | None,
    "candidate_windows": list | None,
    "research_notes": list | None,
    "interview_state": dict | None,
    "clear_fields": list | None,
}


def _build_context_patch_model() -> type[BaseModel]:
    """按 ALLOWED_CONTEXT_FIELDS 动态生成 strict + extra=forbid 的 patch 模型。

    默认每字段 ``str | None``；_NON_STR_FIELD_TYPES 里的按具体类型。与
    sanitize_context_patch 的白名单/类型规则同源（字段名取自同一常量）。
    """
    fields = {
        name: (_NON_STR_FIELD_TYPES.get(name, str | None), None)
        for name in ALLOWED_CONTEXT_FIELDS
    }
    return create_model(
        "ContextPatch",
        __config__=ConfigDict(strict=True, extra="forbid"),
        **fields,
    )


# 审计二 R05：strict + extra=forbid——类型不符/白名单外键交 Pydantic 折 422。
ContextPatch = _build_context_patch_model()


class AdoptInformantRequest(BaseModel):
    candidate: dict


class ConfirmProposalRequest(BaseModel):
    """审计二 R09：确认必须指定文档（doc_id），不再隐式确认『最新一份』。"""

    doc_id: str


def _guarded_state(repo, project_id: str, owner: str | None) -> None:
    checked_owned_project(project_id, repo, owner)


def _transition(repo, project_id: str):
    t = check_and_advance(repo, project_id)
    return {"from": t["from"], "to": t["to"]} if t else None


@router.put("/projects/{project_id}/research-context")
async def update_research_context(project_id: str, body: ContextPatch,
                                  repo=Depends(get_repo),
                                  owner: str | None = Depends(owner_from_request)):
    _guarded_state(repo, project_id, owner)
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
async def confirm_field_site(project_id: str, site_id: str, repo=Depends(get_repo),
                             owner: str | None = Depends(owner_from_request)):
    _guarded_state(repo, project_id, owner)
    if not repo.confirm_field_site(project_id, site_id):
        raise HTTPException(404, f"田野点不存在: {site_id}")
    record = next((s for s in repo.list_field_sites(project_id)
                   if s.get("id") == site_id), {})
    return {"field_site": record,
            "phase_transition": _transition(repo, project_id)}


@router.post("/projects/{project_id}/informants", status_code=201)
async def adopt_informant(project_id: str, body: AdoptInformantRequest,
                          repo=Depends(get_repo),
                          owner: str | None = Depends(owner_from_request)):
    _guarded_state(repo, project_id, owner)
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
async def confirm_proposal(project_id: str, body: ConfirmProposalRequest,
                           repo=Depends(get_repo),
                           owner: str | None = Depends(owner_from_request)):
    _guarded_state(repo, project_id, owner)
    try:
        # 审计二 R09：四重校验在 kernel（Task 8）；此处只做 code→HTTP 映射
        confirmation = repo.confirm_proposal(project_id, body.doc_id)
    except ProposalConfirmError as exc:
        status = {"not_found": 404, "wrong_kind": 400,
                  "dep_hash_missing": 409, "dep_hash_mismatch": 409}[exc.code]
        raise HTTPException(status, str(exc)) from None
    return {"confirmation": confirmation, "proposal_confirmed": True,
            "phase_transition": _transition(repo, project_id)}
