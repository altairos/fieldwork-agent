"""RAG 底座离线契约：来源定位、隔离、撤回、重建、证据链与外发策略。"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fieldwork_agent.kernel.knowledge.chunking import split_document
from fieldwork_agent.kernel.knowledge.corpus import CorpusCatalog, KnowledgeCorpus
from fieldwork_agent.kernel.knowledge.models import (
    ClaimEvidence,
    EntityAlias,
    EvidenceBundle,
    KnowledgeClaim,
    KnowledgeDocument,
    KnowledgeEntity,
    KnowledgeRelation,
    RetrievalRequest,
)
from fieldwork_agent.kernel.knowledge.normalize import content_hash, normalize_name
from fieldwork_agent.kernel.knowledge.policy import require_remote_processing
from fieldwork_agent.kernel.repository.knowledge_index import KnowledgeIndex
from pydantic import ValidationError

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def document(content="历史材料与田野观察需要互证。", **overrides):
    return KnowledgeDocument(**{
        "scope": "domain", "doc_id": "doc", "version": 1, "title": "资料方法",
        "content": content, "content_hash": content_hash(content),
        "source_type": "academic", "source_description": "离线测试材料",
        "reading_level": "excerpt_read", "review_status": "published",
        "reviewed_by": "test-reviewer", "reviewed_at": NOW - timedelta(days=1),
        **overrides,
    })


def corpus(doc=None, *, with_relation=False, **overrides):
    doc = doc or document()
    namespace = {"scope": doc.scope, "project_id": doc.project_id}
    chunks = split_document(doc)
    data = dict(**namespace, corpus_id="test", version="1", coverage_note="仅离线测试",
                documents=(doc,), chunks=chunks)
    if with_relation:
        data.update(
            entities=(KnowledgeEntity(**namespace, entity_id="tradition", entity_type="tradition",
                                      canonical_name="历史人类学"),
                      KnowledgeEntity(**namespace, entity_id="university", entity_type="institution",
                                      canonical_name="示例大学")),
            aliases=(EntityAlias(**namespace, alias="学术简称", normalized_alias=normalize_name("学术简称"),
                                 entity_id="tradition"),),
            claims=(KnowledgeClaim(**namespace, claim_id="claim", statement="测试关系主张",
                                   claim_type="interpretation", review_status="published",
                                   reviewed_by="test-reviewer", reviewed_at=NOW - timedelta(days=1)),),
            claim_evidence=tuple(ClaimEvidence(
                **namespace, claim_id="claim", chunk_id=chunk.chunk_id,
                doc_version=chunk.doc_version, locator=chunk.locator, evidence_role="supports")
                for chunk in chunks),
            relations=(KnowledgeRelation(**namespace, relation_id="relation", subject_id="tradition",
                                         predicate="important_research_hub", object_id="university",
                                         claim_id="claim"),),
        )
    return KnowledgeCorpus(**{**data, **overrides})


def request(query="田野观察", **overrides):
    return RetrievalRequest(**{
        "request_id": "request", "project_id": "p1", "query": query, "as_of": NOW, **overrides,
    })


def revised(source, **updates):
    return KnowledgeCorpus.model_validate({**source.model_dump(mode="json"), **updates})


def test_locator_rejects_end_past_source():
    source = corpus()
    bad = source.model_dump(mode="json")
    bad["chunks"][0]["locator"]["char_end"] += 1
    with pytest.raises(ValidationError, match="定位"):
        KnowledgeCorpus.model_validate(bad)


def test_chunk_sections_do_not_use_later_headings():
    doc = document("# 方法\n\n观察段落。\n### 材料\n族谱段落。\n## 伦理\n\n同意段落。")
    chunks = split_document(doc)
    assert [chunk.section_path for chunk in chunks] == [("方法",), ("方法", "材料"), ("方法", "伦理")]
    assert "族谱" not in chunks[0].text and "观察" not in chunks[1].text
    for chunk in chunks:
        loc = chunk.locator
        assert doc.content[loc.char_start:loc.char_end] == chunk.text
        assert content_hash(chunk.text) == chunk.content_hash
    assert corpus(doc).chunks == chunks


def test_chunk_soft_budget_keeps_paragraph_and_marks_overflow():
    chunks = split_document(document("完整段落不能截断。\n\n下一段。"), max_chars=4)
    assert chunks[0].text == "完整段落不能截断。" and chunks[0].over_char_budget
    assert not chunks[1].over_char_budget
    assert split_document(document(reading_level="metadata_only")) == ()


@pytest.mark.parametrize("overrides", [
    {"scope": "project"}, {"project_id": "p1"}, {"scope": "live"},
    {"source_type": "user_note"}, {"review_status": "user_provided"},
    {"reviewed_by": None}, {"source_url": "https://user:secret@example.org/source"},
    {"content_hash": "wrong"}, {"published_at": "2026-01-01T12:00:00"},
])
def test_invalid_source_contract(overrides):
    with pytest.raises(ValidationError):
        document(**overrides)


def test_corpus_references_cannot_cross_project_or_point_to_missing_records():
    source = corpus(with_relation=True)
    for group, updates in [
        ("aliases", {"scope": "project", "project_id": "p2"}),
        ("aliases", {"entity_id": "missing"}),
        ("claim_evidence", {"doc_version": 2}),
        ("relations", {"subject_id": "university"}),
    ]:
        bad = source.model_dump(mode="json")
        bad[group][0].update(updates)
        with pytest.raises(ValidationError):
            KnowledgeCorpus.model_validate(bad)
    with pytest.raises(ValidationError):
        revised(source, claim_evidence=[])
    with pytest.raises(ValueError, match="冲突"):
        CorpusCatalog([source, revised(source, corpus_id="another")])


def test_alias_relation_returns_complete_original_evidence(tmp_path):
    source = corpus(with_relation=True)
    catalog = CorpusCatalog([source])
    index = KnowledgeIndex(tmp_path / "index.sqlite")
    generation = index.rebuild(catalog)
    bundle = index.search(request("学术简称"), catalog)
    assert bundle.index_generation == generation and bundle.coverage == "available"
    assert bundle.items[0].excerpt == source.documents[0].content
    assert bundle.items[0].claim_ids == ("claim",)
    assert bundle.relation_paths[0].evidence_ids == (bundle.items[0].evidence_id,)
    assert index.search(request("学术简称"), catalog) == bundle
    assert index.rebuild(catalog) == generation


def test_identical_local_ids_are_isolated_by_scope_and_project(tmp_path):
    sources = [corpus(document(title="公共", verified_at=NOW))]
    sources += [corpus(document(scope="project", project_id=pid, title=pid)) for pid in ("p1", "p2")]
    catalog = CorpusCatalog(sources)
    index = KnowledgeIndex(tmp_path / "index.sqlite")
    index.rebuild(catalog)
    bundle = index.search(request(), catalog)
    assert {item.title for item in bundle.items} == {"公共", "p1"}
    assert next(item for item in bundle.items if item.title == "公共").freshness_status == "verified"
    assert {item.title for item in index.search(request(allowed_scopes=("project",)), catalog).items} == {"p1"}
    assert {item.title for item in index.search(request(project_id="p2"), catalog).items} == {"公共", "p2"}
    bad = bundle.model_dump(mode="json")
    next(item for item in bad["items"] if item["scope"] == "project")["project_id"] = "p2"
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(bad)


@pytest.mark.parametrize("updates", [{"deleted": True}, {"review_status": "rejected"}, {"version": 2}])
def test_source_changes_stop_stale_results_before_rebuild(tmp_path, updates):
    source = corpus()
    catalog = CorpusCatalog([source])
    index = KnowledgeIndex(tmp_path / "index.sqlite")
    index.rebuild(catalog)
    new_doc = document(**updates)
    current = CorpusCatalog([corpus(new_doc)])
    bundle = index.search(request(), current)
    assert not bundle.items and "source_changed_rebuild_required" in bundle.degraded
    removed = index.search(request(), CorpusCatalog([]))
    assert not removed.items and "source_changed_rebuild_required" in removed.degraded


def test_rebuild_removes_old_private_projections(tmp_path):
    source = corpus(document("已删除的私人记录。", scope="project", project_id="p1"))
    index = KnowledgeIndex(tmp_path / "index.sqlite")
    old = index.rebuild(CorpusCatalog([source]))
    new = index.rebuild(CorpusCatalog([]))
    assert old != new
    with sqlite3.connect(index.path) as conn:
        assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
        assert conn.execute("SELECT generation_id FROM generations").fetchall() == [(new,)]


def test_rebuild_repairs_corrupt_projection_and_search_degrades(tmp_path):
    catalog = CorpusCatalog([corpus()])
    index = KnowledgeIndex(tmp_path / "index.sqlite")
    generation = index.rebuild(catalog)
    with sqlite3.connect(index.path) as conn:
        conn.execute("UPDATE chunks SET payload='{}'")
    bundle = index.search(request(), catalog)
    assert not bundle.items and "index_record_invalid" in bundle.degraded
    assert index.rebuild(catalog) == generation
    assert index.search(request(), catalog).items


def test_failed_rebuild_keeps_previous_generation(tmp_path, monkeypatch):
    catalog = CorpusCatalog([corpus()])
    index = KnowledgeIndex(tmp_path / "index.sqlite")
    generation = index.rebuild(catalog)

    def fail(*args):
        raise RuntimeError("interrupted rebuild")

    monkeypatch.setattr(index, "_write_corpus", fail)
    with pytest.raises(RuntimeError):
        index.rebuild(CorpusCatalog([corpus(document(version=2))]))
    bundle = index.search(request(), catalog)
    assert bundle.index_generation == generation and bundle.items


def test_relation_path_is_omitted_when_only_part_of_its_evidence_fits(tmp_path):
    source = corpus(document("第一段材料。\n\n第二段材料。"), with_relation=True)
    # 每个段落各一块，防止半条关系链冒充全部依据。
    chunks = split_document(source.documents[0], max_chars=6)
    namespace = {"scope": "domain"}
    source = revised(source, chunks=[chunk.model_dump(mode="json") for chunk in chunks], claim_evidence=[
        ClaimEvidence(**namespace, claim_id="claim", chunk_id=chunk.chunk_id,
                      doc_version=1, locator=chunk.locator, evidence_role="supports").model_dump(mode="json")
        for chunk in chunks])
    catalog = CorpusCatalog([source])
    index = KnowledgeIndex(tmp_path / "index.sqlite")
    index.rebuild(catalog)
    bundle = index.search(request("学术简称", max_items=1), catalog)
    assert len(bundle.items) == 1 and not bundle.relation_paths
    empty = index.search(request("学术简称", max_chars=1), catalog)
    assert not empty.items and "evidence_char_budget" in empty.degraded


def test_future_sources_and_expired_claims_do_not_supply_relations(tmp_path):
    source = corpus(with_relation=True)
    bad_claim = {**source.claims[0].model_dump(mode="json"), "valid_to": NOW - timedelta(days=1)}
    source = revised(source, claims=[bad_claim])
    future = corpus(document(doc_id="future-doc", published_at=NOW + timedelta(days=1)), corpus_id="future")
    catalog = CorpusCatalog([source, future])
    index = KnowledgeIndex(tmp_path / "index.sqlite")
    index.rebuild(catalog)
    assert not index.search(request("学术简称"), catalog).relation_paths
    assert all(item.doc_id != "future-doc" for item in index.search(request(), catalog).items)


def test_index_unbuilt_and_foreign_database_are_safe(tmp_path):
    index = KnowledgeIndex(tmp_path / "new.sqlite")
    assert "index_unbuilt" in index.search(request(), CorpusCatalog([])).degraded
    path = tmp_path / "other.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE user_data (value TEXT)")
        conn.execute("INSERT INTO user_data VALUES ('keep')")
    with pytest.raises(ValueError, match="拒绝"):
        KnowledgeIndex(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM user_data").fetchall() == [("keep",)]


def test_remote_processing_is_denied_by_default_and_requires_each_permission():
    doc = document(scope="project", project_id="p1", external_processing={"embedding_remote": True})
    require_remote_processing(doc, "embedding")
    for purpose in ("rerank", "generation"):
        with pytest.raises(PermissionError):
            require_remote_processing(doc, purpose)
    assert not json.loads(doc.model_dump_json())["external_processing"]["generation_remote"]
