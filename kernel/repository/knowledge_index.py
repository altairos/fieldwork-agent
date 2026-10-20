"""可删除重建的 SQLite 派生索引；本地词法、别名与一跳有据关系召回。

调用者每次提供由当前原资料构造的 CorpusCatalog。版本/审核/删除发生变化时，
旧投影在重建前停止返回；索引不能充当其自身的来源存活证明。
"""
import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fieldwork_agent.kernel.knowledge.corpus import CorpusCatalog, KnowledgeCorpus
from fieldwork_agent.kernel.knowledge.models import (
    EvidenceBundle,
    EvidenceItem,
    KnowledgeChunk,
    RelationPath,
    RetrievalRequest,
)
from fieldwork_agent.kernel.knowledge.normalize import (
    NORMALIZER_VERSION,
    content_hash,
    lexical_terms,
    normalize_name,
)
from fieldwork_agent.kernel.knowledge.policy import searchable_document, valid_claim

_APPLICATION_ID = 0x46574B49
_TABLES = ("documents", "chunks", "entities", "aliases", "claims", "claim_evidence", "relations")
_PROFILE = {"schema": 1, "normalizer": NORMALIZER_VERSION, "retrieval": "bm25-alias-relation-rrf-v1"}


def _authorized(corpus: KnowledgeCorpus, request: RetrievalRequest) -> bool:
    return (corpus.scope in request.allowed_scopes
            and (corpus.scope != "project" or corpus.project_id == request.project_id))


def _alias_in_query(alias: str, query: str) -> bool:
    name = normalize_name(alias)
    if re.search(r"[\u3400-\u9fff]", name):
        return name in query
    return re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", query) is not None


class KnowledgeIndex:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn:
            app_id = conn.execute("PRAGMA application_id").fetchone()[0]
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app_id not in (0, _APPLICATION_ID) or app_id == 0 and tables:
                raise ValueError("目标不是知识派生索引，拒绝改写其他数据库")
            if app_id == _APPLICATION_ID and version != 1:
                raise ValueError("不支持的知识索引schema；请从原资料重建到新文件")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS generations (
                    generation_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, profile TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS active_generation (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS corpora (
                    generation_id TEXT NOT NULL, corpus_key TEXT NOT NULL, scope TEXT NOT NULL,
                    project_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    PRIMARY KEY(generation_id, corpus_key));
                CREATE INDEX IF NOT EXISTS idx_corpora_scope ON corpora(generation_id, scope, project_id);
            """)
            for table in _TABLES:
                conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (generation_id TEXT NOT NULL, corpus_key TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(generation_id, corpus_key, ordinal))")
            conn.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            conn.execute("PRAGMA user_version=1")
            conn.commit()

    def _write_corpus(self, conn, generation: str, corpus: KnowledgeCorpus) -> None:
        conn.execute("INSERT INTO corpora VALUES (?,?,?,?,?)", (
            generation, corpus.key, corpus.scope, corpus.project_id or "", corpus.fingerprint))
        for table in _TABLES:
            conn.executemany(f"INSERT INTO {table} VALUES (?,?,?,?)", [
                (generation, corpus.key, ordinal, record.model_dump_json())
                for ordinal, record in enumerate(getattr(corpus, table))])

    def rebuild(self, catalog: CorpusCatalog) -> str:
        catalog = CorpusCatalog(list(catalog.corpora.values()))
        signature = json.dumps({"profile": _PROFILE, "corpora": sorted(
            (key, corpus.fingerprint) for key, corpus in catalog.corpora.items())}, sort_keys=True)
        generation = "gen-" + content_hash(signature)[:24]
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            # 相同来源也重新投影，确保 rebuild 能修复损坏/缺失的派生记录。
            for table in (*_TABLES, "corpora"):
                conn.execute(f"DELETE FROM {table} WHERE generation_id=?", (generation,))
            conn.execute("INSERT OR REPLACE INTO generations VALUES (?,?,?)", (
                generation, datetime.now(timezone.utc).isoformat(), json.dumps(_PROFILE, sort_keys=True)))
            for corpus in sorted(catalog.corpora.values(), key=lambda item: item.key):
                self._write_corpus(conn, generation, corpus)
            conn.execute("INSERT OR REPLACE INTO active_generation VALUES (1,?)", (generation,))
            # 新投影与切换原子提交，清理旧代的私人正文及派生记录。
            # 搜索不依赖历史代；失败时整个事务回滚，原有效代仍可用。
            for table in (*_TABLES, "corpora", "generations"):
                conn.execute(f"DELETE FROM {table} WHERE generation_id != ?", (generation,))
        return generation

    def search(self, request: RetrievalRequest, catalog: CorpusCatalog) -> EvidenceBundle:
        """当前目录由宿主从原资料读取；本地搜索不会执行任何远程处理。"""
        # 重新校验可变容器；禁止model_copy(update=...)等未校验构造绕过scope约束。
        request = RetrievalRequest.model_validate(request.model_dump(mode="json"))
        catalog = CorpusCatalog(list(catalog.corpora.values()))
        current = {key: corpus for key, corpus in catalog.corpora.items() if _authorized(corpus, request)}
        degraded: set[str] = set()
        chunks: dict[tuple[str, str], KnowledgeChunk] = {}
        ready: dict[str, KnowledgeCorpus] = {}
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT generation_id FROM active_generation WHERE singleton=1").fetchone()
            generation = row[0] if row else "unbuilt"
            if row is None:
                degraded.add("index_unbuilt")
            placeholders = ",".join("?" for _ in request.allowed_scopes)
            rows = conn.execute(f"SELECT corpus_key, fingerprint FROM corpora WHERE generation_id=? AND scope IN ({placeholders}) AND (scope!='project' OR project_id=?)",
                                (generation, *request.allowed_scopes, request.project_id)).fetchall()
            for key, fingerprint in rows:
                corpus = current.get(key)
                if corpus is None or corpus.fingerprint != fingerprint:
                    degraded.add("source_changed_rebuild_required")
                    continue
                ready[key] = corpus
                source_chunks = {chunk.chunk_id: chunk for chunk in corpus.chunks}
                source_docs = {doc.doc_id: doc for doc in corpus.documents}
                for (payload,) in conn.execute("SELECT payload FROM chunks WHERE generation_id=? AND corpus_key=? ORDER BY ordinal", (generation, key)):
                    try:
                        chunk = KnowledgeChunk.model_validate_json(payload)
                    except ValueError:
                        degraded.add("index_record_invalid")
                        continue
                    if chunk != source_chunks.get(chunk.chunk_id):
                        degraded.add("index_record_invalid")
                        continue
                    doc = source_docs[chunk.doc_id]
                    if (searchable_document(doc)
                            and not any(value and value > request.as_of for value in (doc.published_at, doc.retrieved_at))):
                        chunks[key, chunk.chunk_id] = chunk
            if set(current) - set(ready):
                degraded.add("source_changed_rebuild_required")
        bundle = self._retrieve(request, generation, ready, chunks, degraded)
        return bundle

    def _retrieve(self, request, generation, corpora, chunks, degraded):
        docs = {(key, doc.doc_id): doc for key, corpus in corpora.items() for doc in corpus.documents}
        query_terms = set(lexical_terms(request.query))
        projections = {key: Counter(lexical_terms(chunk.text + " " + docs[key[0], chunk.doc_id].title
                                                 + " " + chunk.contextual_note)) for key, chunk in chunks.items()}
        frequency = Counter(term for terms in projections.values() for term in query_terms if term in terms)
        average = sum(sum(terms.values()) for terms in projections.values()) / max(len(projections), 1)
        lexical = {}
        for key, terms in projections.items():
            length = sum(terms.values())
            score = sum(math.log(1 + (len(projections) - frequency[term] + .5) / (frequency[term] + .5))
                        * terms[term] * 2.2 / (terms[term] + 1.2 * (.25 + .75 * length / max(average, 1)))
                        for term in query_terms if terms[term])
            if score > 0:
                lexical[key] = score
        relation_hits: set[tuple[str, str]] = set()
        relation_groups = []
        unresolved = []
        alias_candidates: dict[str, set[tuple[str, str]]] = {}
        linked_claims: dict[tuple[str, str], set[str]] = {}
        query = normalize_name(request.query)
        for key, corpus in corpora.items():
            claims = {claim.claim_id: claim for claim in corpus.claims if valid_claim(claim, request.as_of)}
            matches: dict[str, set[str]] = {}
            for alias, entity_id in [(alias.alias, alias.entity_id) for alias in corpus.aliases] + [
                    (entity.canonical_name, entity.entity_id) for entity in corpus.entities]:
                if _alias_in_query(alias, query):
                    matches.setdefault(normalize_name(alias), set()).add(entity_id)
            for alias, ids in matches.items():
                alias_candidates.setdefault(alias, set()).update((key, entity_id) for entity_id in ids)
            matched_ids = set().union(*matches.values()) if matches else set()
            for evidence in corpus.claim_evidence:
                if evidence.claim_id in claims and (key, evidence.chunk_id) in chunks:
                    linked_claims.setdefault((key, evidence.chunk_id), set()).add(evidence.claim_id)
            for relation in corpus.relations:
                if (relation.claim_id not in claims
                        or not {relation.subject_id, relation.object_id} & matched_ids
                        or relation.valid_from and request.as_of < relation.valid_from
                        or relation.valid_to and request.as_of > relation.valid_to):
                    continue
                links = [(key, evidence.chunk_id) for evidence in corpus.claim_evidence
                         if evidence.claim_id == relation.claim_id]
                # 证据链缺一部分则不输出关系，不用单个高分片段代替整套依据。
                if links and all(link in chunks for link in links):
                    relation_hits.update(links)
                    relation_groups.append((key, relation, set(links)))
        # 词法与有据关系按名次融合；分数只用于内部排序，不输出为可信概率。
        fused: Counter = Counter()
        for ranking in (sorted(lexical, key=lambda key: (-lexical[key], key)), sorted(relation_hits)):
            for rank, key in enumerate(ranking, 1):
                fused[key] += 1 / (60 + rank)
        items = []
        selected: dict[tuple[str, str], str] = {}
        used_chars = 0
        for key in sorted(fused, key=lambda item: (-fused[item], item)):
            if len(items) >= request.max_items:
                break
            chunk, doc = chunks[key], docs[key[0], chunks[key].doc_id]
            if used_chars + len(chunk.text) > request.max_chars:
                degraded.add("evidence_char_budget")
                continue
            claim_ids = tuple(sorted(linked_claims.get(key, ())))
            due = any(claim.review_due_at and claim.review_due_at <= request.as_of
                      for claim in corpora[key[0]].claims if claim.claim_id in claim_ids)
            evidence_id = f"E{len(items) + 1}"
            items.append(EvidenceItem(
                evidence_id=evidence_id, scope=doc.scope, project_id=doc.project_id,
                doc_id=doc.doc_id, chunk_id=chunk.chunk_id, doc_version=doc.version,
                content_hash=chunk.content_hash, excerpt=chunk.text, locator=chunk.locator,
                title=doc.title, source_url=doc.source_url, source_description=doc.source_description,
                source_type=doc.source_type, reading_level=doc.reading_level,
                verified_at=doc.verified_at, external_processing=doc.external_processing,
                claim_ids=claim_ids, freshness_status="review_due" if due else
                "verified" if doc.verified_at and doc.verified_at <= request.as_of else "unknown"))
            selected[key] = evidence_id
            used_chars += len(chunk.text)
        paths = [RelationPath(scope=corpora[key].scope, project_id=corpora[key].project_id,
                              relation_ids=(relation.relation_id,),
                              evidence_ids=tuple(selected[link] for link in sorted(links)))
                 for key, relation, links in relation_groups if links <= selected.keys()]
        unresolved = [f"别名「{alias}」存在多个候选，尚未消歧"
                      for alias, ids in alias_candidates.items() if len(ids) > 1]
        signature = json.dumps({"request": request.model_dump(mode="json"), "generation": generation,
                                "items": [item.model_dump(mode="json") for item in items],
                                "paths": [path.model_dump(mode="json") for path in paths],
                                "degraded": sorted(degraded), "unresolved": sorted(unresolved)}, sort_keys=True)
        return EvidenceBundle(
            snapshot_id="snapshot-" + content_hash(signature)[:24], request_id=request.request_id,
            project_id=request.project_id, index_generation=generation, items=tuple(items),
            relation_paths=tuple(paths), unresolved=tuple(sorted(set(unresolved))),
            coverage="available" if items else "insufficient" if fused or degraded else "empty",
            degraded=tuple(sorted(degraded)))
