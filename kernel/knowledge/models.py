"""知识 schema v1：原文、主张、关系及本轮证据分别建模。

只校验可确定的结构、定位和来源约束；是否蕴含某个学术主张仍需人工审核。
"""
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fieldwork_agent.kernel.knowledge.normalize import content_hash, normalize_name
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Scope = Literal["domain", "project", "live"]
ReviewStatus = Literal["draft", "published", "rejected", "user_provided"]
EntityType = Literal["tradition", "concept", "person", "institution", "department", "center", "work", "method", "region", "research"]
Predicate = Literal["representative_of", "studies", "uses_method", "authored", "part_of", "affiliated_with", "developed_at", "important_research_hub"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class ScopedRecord(Contract):
    scope: Scope
    project_id: NonEmpty | None = None

    @model_validator(mode="after")
    def scope_matches_project(self):
        if (self.scope == "project") != (self.project_id is not None):
            raise ValueError("project范围必须指定project_id；公共范围不得绑定项目")
        return self

    @property
    def namespace(self) -> tuple[str, str | None]:
        return self.scope, self.project_id


class ExternalProcessingPolicy(Contract):
    embedding_remote: bool = False
    rerank_remote: bool = False
    generation_remote: bool = False


class TextLocator(Contract):
    kind: Literal["text"] = "text"
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.char_end <= self.char_start or self.line_end < self.line_start:
            raise ValueError("定位范围必须有序且非空")
        return self


class KnowledgeDocument(ScopedRecord):
    doc_id: NonEmpty
    version: int = Field(ge=1)
    title: NonEmpty
    content: str
    content_hash: NonEmpty
    source_type: Literal["academic", "institutional", "user_note", "teaching_example", "web_snapshot"]
    source_description: NonEmpty
    source_url: str | None = None
    author: str | None = None
    language: NonEmpty = "und"
    published_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime | None = None
    verified_at: AwareDatetime | None = None
    review_status: ReviewStatus = "draft"
    reviewed_by: NonEmpty | None = None
    reviewed_at: AwareDatetime | None = None
    reading_level: Literal["metadata_only", "abstract_only", "excerpt_read", "fulltext_read"] = "metadata_only"
    external_processing: ExternalProcessingPolicy = Field(default_factory=ExternalProcessingPolicy)
    deleted: bool = False

    @model_validator(mode="after")
    def source_is_consistent(self):
        if content_hash(self.content) != self.content_hash:
            raise ValueError("文档content_hash与原文不符")
        if self.source_url is not None:
            url = urlsplit(self.source_url)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                raise ValueError("source_url须为无内嵌凭据的HTTP(S)来源地址")
        if self.scope == "live" and (self.retrieved_at is None or self.verified_at is None):
            raise ValueError("live资料必须有抓取与核验时间")
        if self.review_status == "user_provided" and self.scope != "project":
            raise ValueError("用户资料只能位于project范围")
        if self.source_type == "user_note" and self.scope != "project":
            raise ValueError("私人笔记不能直接进入公共范围")
        if self.review_status == "published" and (self.reviewed_by is None or self.reviewed_at is None):
            raise ValueError("发布资料须保留审核者与审核时间")
        return self


class KnowledgeChunk(ScopedRecord):
    chunk_id: NonEmpty
    doc_id: NonEmpty
    doc_version: int = Field(ge=1)
    text: Annotated[str, StringConstraints(min_length=1)]
    content_hash: NonEmpty
    locator: TextLocator
    section_path: tuple[str, ...] = ()
    contextual_note: str = ""
    token_count: int | None = Field(default=None, ge=1)
    over_char_budget: bool = False

    @model_validator(mode="after")
    def hash_matches_text(self):
        if content_hash(self.text) != self.content_hash:
            raise ValueError("chunk哈希与正文不符")
        return self


class KnowledgeEntity(ScopedRecord):
    entity_id: NonEmpty
    entity_type: EntityType
    canonical_name: NonEmpty
    description: str = ""


class EntityAlias(ScopedRecord):
    alias: NonEmpty
    normalized_alias: NonEmpty
    entity_id: NonEmpty
    language: str = "und"
    disambiguation_note: str = ""

    @model_validator(mode="after")
    def normalization_matches(self):
        if self.normalized_alias != normalize_name(self.alias):
            raise ValueError("normalized_alias与当前归一化版本不符")
        return self


class KnowledgeClaim(ScopedRecord):
    claim_id: NonEmpty
    statement: NonEmpty
    claim_type: Literal["fact", "interpretation", "disputed", "recommendation"]
    review_status: ReviewStatus = "draft"
    reviewed_by: NonEmpty | None = None
    reviewed_at: AwareDatetime | None = None
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    verified_at: AwareDatetime | None = None
    review_due_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def review_and_dates(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("主张有效期倒置")
        if self.review_status == "published" and (self.reviewed_by is None or self.reviewed_at is None):
            raise ValueError("发布主张须保留审核者与审核时间")
        return self


class ClaimEvidence(ScopedRecord):
    claim_id: NonEmpty
    chunk_id: NonEmpty
    doc_version: int = Field(ge=1)
    locator: TextLocator
    evidence_role: Literal["supports", "opposes", "qualifies"]


class KnowledgeRelation(ScopedRecord):
    relation_id: NonEmpty
    subject_id: NonEmpty
    predicate: Predicate
    object_id: NonEmpty
    claim_id: NonEmpty
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("关系有效期倒置")
        return self


class RetrievalRequest(Contract):
    """仅供宿主构造；模型不能提交allowed_scopes或project_id作为授权。"""
    request_id: NonEmpty
    project_id: NonEmpty
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    intent: str = "research_background"
    phase: str = ""
    allowed_scopes: tuple[Scope, ...] = ("domain", "project")
    as_of: AwareDatetime
    max_items: int = Field(default=8, ge=1, le=20)
    max_chars: int = Field(default=12000, ge=1, le=50000)

    @model_validator(mode="after")
    def scopes_are_unique(self):
        if not self.allowed_scopes or len(set(self.allowed_scopes)) != len(self.allowed_scopes):
            raise ValueError("allowed_scopes须为非空、不重复的范围列表")
        return self


class EvidenceItem(ScopedRecord):
    evidence_id: NonEmpty
    doc_id: NonEmpty
    chunk_id: NonEmpty
    doc_version: int = Field(ge=1)
    content_hash: NonEmpty
    excerpt: Annotated[str, StringConstraints(min_length=1)]
    locator: TextLocator
    title: NonEmpty
    source_url: str | None = None
    source_description: NonEmpty
    source_type: str
    reading_level: str
    verified_at: AwareDatetime | None = None
    external_processing: ExternalProcessingPolicy
    claim_ids: tuple[str, ...] = ()
    freshness_status: Literal["verified", "unknown", "review_due"] = "unknown"

    @model_validator(mode="after")
    def excerpt_matches_locator(self):
        if (content_hash(self.excerpt) != self.content_hash
                or self.locator.char_end - self.locator.char_start != len(self.excerpt)):
            raise ValueError("证据摘录、哈希与定位长度不符")
        return self


class RelationPath(ScopedRecord):
    relation_ids: tuple[NonEmpty, ...] = Field(min_length=1, max_length=2)
    evidence_ids: tuple[NonEmpty, ...] = Field(min_length=1)


class EvidenceBundle(Contract):
    schema_version: Literal[1] = 1
    snapshot_id: NonEmpty
    request_id: NonEmpty
    project_id: NonEmpty
    index_generation: NonEmpty
    items: tuple[EvidenceItem, ...] = ()
    relation_paths: tuple[RelationPath, ...] = ()
    unresolved: tuple[str, ...] = ()
    coverage: Literal["available", "insufficient", "empty"] = "empty"
    degraded: tuple[str, ...] = ()

    @model_validator(mode="after")
    def evidence_is_scoped(self):
        ids = {item.evidence_id for item in self.items}
        if len(ids) != len(self.items):
            raise ValueError("同一快照证据ID不得重复")
        for record in (*self.items, *self.relation_paths):
            if record.scope == "project" and record.project_id != self.project_id:
                raise ValueError("证据快照不得混入其他项目资料")
        if any(not set(path.evidence_ids) <= ids for path in self.relation_paths):
            raise ValueError("关系路径缺少本快照的完整证据")
        by_id = {item.evidence_id: item for item in self.items}
        if any(by_id[item_id].namespace != path.namespace
               for path in self.relation_paths for item_id in path.evidence_ids):
            raise ValueError("关系路径与支撑证据范围不一致")
        return self


class EvidenceManifest(Contract):
    schema_version: Literal[1] = 1
    project_id: NonEmpty
    snapshot_id: NonEmpty
    document_id: NonEmpty
    evidence_ids: tuple[NonEmpty, ...]
    source_versions: tuple[tuple[Scope, str | None, str, int, str], ...]

    @model_validator(mode="after")
    def references_are_scoped(self):
        for scope, project_id, _, version, digest in self.source_versions:
            if (scope == "project" and project_id != self.project_id
                    or scope != "project" and project_id is not None
                    or version < 1 or len(digest) != 64):
                raise ValueError("manifest包含非法范围、版本或哈希")
        return self


class GroundingTrace(Contract):
    turn_id: NonEmpty
    context_fingerprint: NonEmpty
    hit_ids: tuple[str, ...] = ()
    selected_ids: tuple[str, ...] = ()
    provider_versions: dict[str, str] = Field(default_factory=dict)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    failure_codes: tuple[str, ...] = ()
