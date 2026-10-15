"""提案导出路由（spec §3.2 exports）。"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from fieldwork_agent.web_host.api.guard import checked_pid
from fieldwork_agent.web_host.deps import get_repo

router = APIRouter(tags=["exports"])


@router.get("/projects/{project_id}/proposal")
async def get_proposal(project_id: str, repo=Depends(get_repo)):
    # 终审 C1（HTTP 侧）：守卫必须先于 repo（否则穿越 id 可越根读状态/提案）
    project_id = checked_pid(project_id, repo)
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
    project_id = checked_pid(project_id, repo)
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    doc = repo.get_latest_proposal(project_id)
    if doc is None:
        raise HTTPException(404, "尚无提案文档")
    content = doc.get("content", "")
    # project_id 已过守卫（无分隔符/null 字节），进 Content-Disposition 回落文件名安全；
    # doc["filename"] 由 repo.save_proposal 内部生成（kind_version_时间戳.md），非客户端可控
    filename = doc.get("filename", f"{project_id}_proposal.md")
    return PlainTextResponse(
        content, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/projects/{project_id}/proposals")
async def list_proposals(project_id: str, repo=Depends(get_repo)):
    project_id = checked_pid(project_id, repo)
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    return [{"id": p["id"], "kind": p.get("kind"), "version": p.get("version"),
             "created_at": p.get("created_at")} for p in repo.list_proposals(project_id)]
