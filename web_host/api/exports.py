"""提案导出路由（spec §3.2 exports）。"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from fieldwork_agent.web_host.deps import get_repo

router = APIRouter(tags=["exports"])


@router.get("/projects/{project_id}/proposal")
async def get_proposal(project_id: str, repo=Depends(get_repo)):
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    doc = repo.get_latest_proposal(project_id)
    if doc is None:
        raise HTTPException(404, "尚无提案文档")
    content = doc.get("content", "")
    sections = [ln.lstrip("# ").strip() for ln in content.splitlines()
                if ln.startswith("## ")]
    return {"id": doc.get("id"), "markdown": content,
            "kind": doc.get("kind", "proposal"),
            "version": doc.get("version", "draft"), "sections": sections}


@router.get("/projects/{project_id}/proposal/download")
async def download_proposal(project_id: str, repo=Depends(get_repo)):
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    doc = repo.get_latest_proposal(project_id)
    if doc is None:
        raise HTTPException(404, "尚无提案文档")
    content = doc.get("content", "")
    filename = doc.get("filename", f"{project_id}_proposal.md")
    return PlainTextResponse(
        content, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/projects/{project_id}/proposals")
async def list_proposals(project_id: str, repo=Depends(get_repo)):
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    return [{"id": p["id"], "kind": p.get("kind"), "version": p.get("version"),
             "created_at": p.get("created_at")} for p in repo.list_proposals(project_id)]
