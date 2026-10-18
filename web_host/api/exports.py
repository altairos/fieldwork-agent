"""提案导出路由（spec §3.2 exports）。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from fieldwork_agent.web_host.api.guard import checked_owned_project
from fieldwork_agent.web_host.api.owner import owner_from_request
from fieldwork_agent.web_host.deps import get_repo
from fieldwork_agent.web_host.export_formats import filename_for, to_docx_bytes, to_text

router = APIRouter(tags=["exports"])

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _proposal_payload(doc: dict) -> dict:
    """落盘记录 → 读取端点响应（get_proposal 与 by-id 同构，F13）。"""
    content = doc.get("content", "")
    sections = [ln.lstrip("# ").strip() for ln in content.splitlines()
                if ln.startswith("## ")]
    return {"id": doc.get("id"), "markdown": content,
            "kind": doc.get("kind", "proposal"),
            "version": doc.get("version", "draft"), "sections": sections}


def _format_proposal_response(content: str, format: str, filename: str) -> Response:
    """md/docx/txt 三分支持（spec §3.2；docx 失败整体降级 txt，绝不让 500 冒泡）。"""
    if format == "md":
        return PlainTextResponse(
            content, media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    if format == "docx":
        docx_bytes = to_docx_bytes(content)
        if docx_bytes is not None:
            return Response(
                docx_bytes, media_type=DOCX_MEDIA_TYPE,
                headers={"Content-Disposition":
                         f'attachment; filename="{filename_for(filename, "docx")}"'})
        # docx 转换失败：整体降级 txt（绝不让 500 冒泡）
    return PlainTextResponse(
        to_text(content), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename_for(filename, "txt")}"'})


@router.get("/projects/{project_id}/proposal")
async def get_proposal(project_id: str, repo=Depends(get_repo),
                       owner: str | None = Depends(owner_from_request)):
    checked_owned_project(project_id, repo, owner)
    doc = repo.get_latest_proposal(project_id)
    if doc is None:
        raise HTTPException(404, "尚无提案文档")
    return _proposal_payload(doc)


@router.get("/projects/{project_id}/proposal/download")
async def download_proposal(
    project_id: str,
    # 参数名沿用 spec：format（ruff 未启用 A 系列，遮蔽内置不触发，不加 noqa）
    format: Literal["md", "docx", "txt"] = "md",
    repo=Depends(get_repo),
    owner: str | None = Depends(owner_from_request),
):
    checked_owned_project(project_id, repo, owner)
    doc = repo.get_latest_proposal(project_id)
    if doc is None:
        raise HTTPException(404, "尚无提案文档")
    content = doc.get("content", "")
    filename = doc.get("filename", f"{project_id}_proposal.md")
    return _format_proposal_response(content, format, filename)


@router.get("/projects/{project_id}/proposals/{doc_id}")
async def get_proposal_by_id(project_id: str, doc_id: str, repo=Depends(get_repo),
                             owner: str | None = Depends(owner_from_request)):
    checked_owned_project(project_id, repo, owner)
    doc = repo.get_proposal_by_id(project_id, doc_id)
    if doc is None:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    return _proposal_payload(doc)


@router.get("/projects/{project_id}/proposals/{doc_id}/download")
async def download_proposal_by_id(
    project_id: str,
    doc_id: str,
    # 参数名沿用 spec：format（ruff 未启用 A 系列，遮蔽内置不触发，不加 noqa）
    format: Literal["md", "docx", "txt"] = "md",
    repo=Depends(get_repo),
    owner: str | None = Depends(owner_from_request),
):
    checked_owned_project(project_id, repo, owner)
    doc = repo.get_proposal_by_id(project_id, doc_id)
    if doc is None:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    filename = doc.get("filename", f"{project_id}_{doc_id}.md")
    return _format_proposal_response(doc.get("content", ""), format, filename)


@router.get("/projects/{project_id}/proposals")
async def list_proposals(project_id: str, repo=Depends(get_repo),
                         owner: str | None = Depends(owner_from_request)):
    checked_owned_project(project_id, repo, owner)
    return [{"id": p["id"], "kind": p.get("kind"), "version": p.get("version"),
             "created_at": p.get("saved_at")} for p in repo.list_proposals(project_id)]
