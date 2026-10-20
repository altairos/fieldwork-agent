"""版本化资料包与引用完整性校验；所有跨对象引用都留在同一授权命名空间。"""
import json
from pathlib import Path
from typing import Literal

from fieldwork_agent.kernel.knowledge.models import (
    ClaimEvidence,
    EntityAlias,
    KnowledgeChunk,
    KnowledgeClaim,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeRelation,
    NonEmpty,
    ScopedRecord,
)
from fieldwork_agent.kernel.knowledge.normalize import content_hash
from pydantic import Field, model_validator

_RELATION_TYPES = {
    "representative_of": ({"person"}, {"tradition"}),
    "studies": ({"person", "tradition", "research"}, {"concept", "region", "research"}),
    "uses_method": ({"person", "tradition", "research"}, {"method"}),
    "authored": ({"person"}, {"work"}),
    "part_of": ({"department", "center", "institution"}, {"institution", "department"}),
    "affiliated_with": ({"person"}, {"institution", "department", "center"}),
    "developed_at": ({"tradition"}, {"institution", "department", "center"}),
    "important_research_hub": ({"tradition"}, {"institution", "department", "center"}),
}


class KnowledgeCorpus(ScopedRecord):
    schema_version: Literal[1] = 1
    corpus_id: NonEmpty
    version: NonEmpty
    coverage_note: NonEmpty
    change_note: str = ""
    documents: tuple[KnowledgeDocument, ...] = ()
    chunks: tuple[KnowledgeChunk, ...] = ()
    entities: tuple[KnowledgeEntity, ...] = ()
    aliases: tuple[EntityAlias, ...] = ()
    claims: tuple[KnowledgeClaim, ...] = ()
    claim_evidence: tuple[ClaimEvidence, ...] = ()
    relations: tuple[KnowledgeRelation, ...] = ()
    chunker_version: NonEmpty = "paragraph-char-v1"
    max_chunks: int = Field(default=10000, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_references(self):
        groups = (self.documents, self.chunks, self.entities, self.aliases, self.claims,
                  self.claim_evidence, self.relations)
        if any(record.namespace != self.namespace for group in groups for record in group):
            raise ValueError("资料包中的正文、别名、主张和关系必须使用同一范围与项目")
        if len(self.chunks) > self.max_chunks:
            raise ValueError("超出本地索引资料包规模限制")

        def unique(records, key):
            result = {getattr(record, key): record for record in records}
            if len(result) != len(records):
                raise ValueError(f"资料包内{key}不得重复")
            return result

        docs = unique(self.documents, "doc_id")
        chunks = unique(self.chunks, "chunk_id")
        entities = unique(self.entities, "entity_id")
        claims = unique(self.claims, "claim_id")
        unique(self.relations, "relation_id")
        for chunk in self.chunks:
            doc = docs.get(chunk.doc_id)
            if doc is None or chunk.doc_version != doc.version:
                raise ValueError("chunk缺少对应原文版本")
            loc = chunk.locator
            if (loc.char_end > len(doc.content)
                    or doc.content[loc.char_start:loc.char_end] != chunk.text
                    or doc.content[:loc.char_start].count("\n") + 1 != loc.line_start
                    or doc.content[:loc.char_end].count("\n") + 1 != loc.line_end):
                raise ValueError("chunk正文或行/字符定位与原文版本不符")
        for alias in self.aliases:
            if alias.entity_id not in entities:
                raise ValueError("别名指向不存在的实体")
        links: dict[str, list[ClaimEvidence]] = {}
        for evidence in self.claim_evidence:
            chunk = chunks.get(evidence.chunk_id)
            if (evidence.claim_id not in claims or chunk is None
                    or evidence.doc_version != chunk.doc_version or evidence.locator != chunk.locator):
                raise ValueError("主张依据缺少同版本、同定位的原文块")
            links.setdefault(evidence.claim_id, []).append(evidence)
        for claim in self.claims:
            if claim.review_status == "published":
                supported = [link for link in links.get(claim.claim_id, ())
                             if link.evidence_role in {"supports", "qualifies"}]
                if not supported:
                    raise ValueError("发布主张至少需要支持或限定它的原文依据")
                for link in supported:
                    doc = docs[chunks[link.chunk_id].doc_id]
                    if doc.deleted or doc.reading_level == "metadata_only" or doc.review_status != "published":
                        raise ValueError("发布主张不可使用未发布、已删除或只有书目的资料作为依据")
        for relation in self.relations:
            subject, obj, claim = (entities.get(relation.subject_id), entities.get(relation.object_id),
                                   claims.get(relation.claim_id))
            if subject is None or obj is None or claim is None:
                raise ValueError("关系缺少实体或主张")
            subjects, objects = _RELATION_TYPES[relation.predicate]
            if subject.entity_type not in subjects or obj.entity_type not in objects:
                raise ValueError("关系谓词与主客体类型不匹配")
            if self.scope != "project" and claim.review_status != "published":
                raise ValueError("公共关系必须连接已发布且有原文依据的主张")
        return self

    @property
    def key(self) -> str:
        return json.dumps([self.scope, self.project_id, self.corpus_id], ensure_ascii=False)

    @property
    def fingerprint(self) -> str:
        return content_hash(json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False))


class CorpusCatalog:
    """宿主每次从当前原始资料构造；不以派生索引自己的副本冒充存活校验。"""

    def __init__(self, corpora: list[KnowledgeCorpus] | tuple[KnowledgeCorpus, ...]):
        self.corpora: dict[str, KnowledgeCorpus] = {}
        ids: set[tuple] = set()
        for item in corpora:
            corpus = KnowledgeCorpus.model_validate(item.model_dump(mode="json"))
            if corpus.key in self.corpora:
                raise ValueError("当前目录内同一资料包只能有一个有效版本")
            for group, field in ((corpus.documents, "doc_id"), (corpus.chunks, "chunk_id"),
                                 (corpus.entities, "entity_id"), (corpus.claims, "claim_id"),
                                 (corpus.relations, "relation_id")):
                for record in group:
                    key = (*corpus.namespace, field, getattr(record, field))
                    if key in ids:
                        raise ValueError("不同资料包在同一范围使用了冲突的本地ID")
                    ids.add(key)
            self.corpora[corpus.key] = corpus
        if sum(len(corpus.chunks) for corpus in self.corpora.values()) > 10000:
            raise ValueError("当前精确本地索引最多支持10000个正文块")

    @classmethod
    def from_paths(cls, paths: list[Path]) -> "CorpusCatalog":
        return cls([KnowledgeCorpus.model_validate_json(path.read_text(encoding="utf-8")) for path in paths])
