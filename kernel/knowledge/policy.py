"""资料可检索条件与外发限制；相关性分数不覆盖这些条件。"""
from datetime import datetime
from typing import Literal

from fieldwork_agent.kernel.knowledge.models import KnowledgeClaim, KnowledgeDocument


def searchable_document(doc: KnowledgeDocument) -> bool:
    return (not doc.deleted and doc.reading_level != "metadata_only"
            and (doc.review_status == "published"
                 or doc.scope == "project" and doc.review_status == "user_provided"))


def valid_claim(claim: KnowledgeClaim, as_of: datetime) -> bool:
    return (claim.review_status == "published"
            and (claim.valid_from is None or claim.valid_from <= as_of)
            and (claim.valid_to is None or as_of <= claim.valid_to))


def require_remote_processing(doc: KnowledgeDocument, purpose: Literal["embedding", "rerank", "generation"]) -> None:
    if purpose not in {"embedding", "rerank", "generation"}:
        raise ValueError("未知的远程处理用途")
    if not getattr(doc.external_processing, f"{purpose}_remote"):
        raise PermissionError(f"资料未授权{purpose}远程处理")
