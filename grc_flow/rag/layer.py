"""The RAG layer: one place that indexes knowledge into Pinecone and retrieves it,
for the pipelines (agent) and for the analyst.

Namespaces in the one Pinecone index:
  law        Act + Rules, split per provision (citable)
  guidance   obligations register + guidance notes (explanations, never cited)
  org-<id>   each organisation's own documents, re-indexed when they change

Search is hybrid for law (GRC-Ai's BM25 over the same provisions + Pinecone) and
vector-only for the rest; all ranked lists are merged with reciprocal rank fusion.
Law hits always come back with the full ingested provision text, so what the
agent quotes or cites is the gazette text, not a fragment.

Every law/guidance record carries the knowledge version. Hits from an older
version (rag-init not re-run after a change) are ignored, and the checker reports it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from grc_agent.corpus.store import Corpus

from grc_flow.adapters.vectors import VectorIndex
from grc_flow.rag.chunking import (
    Chunk,
    chunk_document,
    chunk_law,
    doc_prefix,
    estimate_tokens,
    safe_id,
)
from grc_flow.rag.sources import guidance_chunks

NS_LAW = "law"
NS_GUIDANCE = "guidance"
RRF_K = 60
SCOPES = ("law", "guidance", "org")


def org_namespace(org_id: str) -> str:
    return f"org-{safe_id(org_id)}"


@dataclass
class Retrieved:
    id: str
    source_type: str  # law | register | guidance | document
    authority: str  # law | guidance | org
    ref: str
    title: str
    heading: str
    text: str
    score: float = 0.0
    via: list[str] = field(default_factory=list)
    doc_id: str = ""
    char_start: int | None = None
    char_end: int | None = None

    def to_dict(self, max_chars: int = 1500) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v not in ("", None, [])}
        out["text"] = self.text[:max_chars]
        return out


class RagLayer:
    def __init__(self, index: VectorIndex, corpus: Corpus, corpus_version: str) -> None:
        self.index = index
        self.corpus = corpus
        self.law_chunks = chunk_law(corpus.chunks)
        self.guidance_chunks = guidance_chunks()
        material = "".join(c.id + c.text for c in self.law_chunks + self.guidance_chunks)
        self.version = hashlib.sha256((corpus_version + material).encode()).hexdigest()[:16]
        self._by_key = {c.key: c for c in corpus.chunks}

    # Indexing ----------------------------------------------------------------------

    def sync_knowledge(self, max_tokens: int | None = None) -> dict[str, Any]:
        """Upsert law and guidance; delete records that no longer exist. Idempotent."""
        too_long = [
            c.id
            for c in self.law_chunks + self.guidance_chunks
            if max_tokens and estimate_tokens(c.text) > max_tokens
        ]
        if too_long:
            raise ValueError(
                f"{len(too_long)} chunk(s) exceed the embedding model's {max_tokens}-token "
                f"limit and would be truncated: {too_long[:5]}"
            )
        report = {}
        for ns, chunks, prefixes in (
            (NS_LAW, self.law_chunks, ("law:",)),
            (NS_GUIDANCE, self.guidance_chunks, ("oblig:", "guide:")),
        ):
            upserted = self.index.upsert(ns, [c.to_record(self.version) for c in chunks])
            current = {c.id for c in chunks}
            stale = [i for p in prefixes for i in self.index.list_ids(ns, p) if i not in current]
            deleted = self.index.delete_ids(ns, stale) if stale else 0
            report[ns] = {
                "chunks": len(chunks),
                "upserted": upserted,
                "deleted_stale": deleted,
                "max_tokens": max(estimate_tokens(c.text) for c in chunks),
            }
        return report

    def index_document(
        self, org_id: str, doc_id: str, text: str, title: str = ""
    ) -> dict[str, Any]:
        """(Re)index one org document: old chunks of the same doc are replaced."""
        ns = org_namespace(org_id)
        version = hashlib.sha256(text.encode()).hexdigest()[:16]
        chunks = chunk_document(doc_id, text, title)
        old = self.index.list_ids(ns, doc_prefix(doc_id))
        current = {c.id for c in chunks}
        leftover = [i for i in old if i not in current]
        upserted = self.index.upsert(ns, [c.to_record(version) for c in chunks]) if chunks else 0
        deleted = self.index.delete_ids(ns, leftover) if leftover else 0
        return {
            "namespace": ns,
            "chunks": len(chunks),
            "upserted": upserted,
            "deleted": deleted,
            "doc_version": version,
            "max_tokens": max((estimate_tokens(c.text) for c in chunks), default=0),
        }

    def delete_document(self, org_id: str, doc_id: str) -> int:
        ns = org_namespace(org_id)
        return self.index.delete_ids(ns, self.index.list_ids(ns, doc_prefix(doc_id)))

    # Retrieval ---------------------------------------------------------------------

    def search(
        self,
        query: str,
        k: int = 5,
        scope: tuple[str, ...] = ("law", "guidance"),
        org_id: str | None = None,
    ) -> list[Retrieved]:
        bad = [s for s in scope if s not in SCOPES]
        if bad:
            raise ValueError(f"Unknown scope {bad}; use {SCOPES}.")
        if "org" in scope and not org_id:
            raise ValueError("Searching org documents needs an org_id.")
        # Fuse BM25 and vector inside law first, then fuse one ranked list per source,
        # so law (two retrievers) doesn't crowd out guidance or the org's documents.
        per_source: list[list[Retrieved]] = []
        if "law" in scope:
            law: dict[str, Retrieved] = {}
            for rank, hit in enumerate(self.corpus.search(query, k * 2), start=1):
                _rrf(law, f"law:{hit.chunk.key}", lambda c=hit.chunk: self._law(c), rank, "bm25")
            for rank, hit in enumerate(self.index.search(NS_LAW, query, k * 2), start=1):
                chunk = self._from_ref(hit.fields.get("ref", ""))
                if chunk is None or hit.fields.get("version") != self.version:
                    continue
                _rrf(law, f"law:{chunk.key}", lambda c=chunk: self._law(c), rank, "vector")
            per_source.append(sorted(law.values(), key=lambda r: -r.score))
        if "guidance" in scope:
            hits = [
                h
                for h in self.index.search(NS_GUIDANCE, query, k * 2)
                if h.fields.get("version") == self.version
            ]
            per_source.append([_from_fields(h, "vector") for h in hits])
        if "org" in scope:
            hits = self.index.search(org_namespace(org_id or ""), query, k * 2)
            per_source.append([_from_fields(h, "vector") for h in hits])

        fused: dict[str, Retrieved] = {}
        for ranked_list in per_source:
            for rank, item in enumerate(ranked_list, start=1):
                kept = fused.setdefault(item.id, item)
                kept.score = (kept.score if kept is not item else 0.0) + 1.0 / (RRF_K + rank)
        ranked = sorted(fused.values(), key=lambda r: -r.score)[:k]
        for r in ranked:
            r.score = round(r.score, 5)
        return ranked

    def _from_ref(self, ref: str):
        from grc_agent.kpis.citations import normalize_citation

        key = normalize_citation(ref) if ref else None
        return self._by_key.get(key) if key else None

    @staticmethod
    def _law(chunk) -> Retrieved:
        return Retrieved(
            id=f"law:{chunk.key}",
            source_type="law",
            authority="law",
            ref=chunk.ref,
            title=chunk.source,
            heading=chunk.heading,
            text=chunk.text,
        )

    def provision(self, ref: str):
        return self.corpus.provision(ref)

    def resolves(self, ref: str) -> bool:
        return self.corpus.index.resolves(ref)

    def expected_counts(self) -> dict[str, int]:
        return {NS_LAW: len(self.law_chunks), NS_GUIDANCE: len(self.guidance_chunks)}


def _rrf(acc: dict[str, Retrieved], key: str, make, rank: int, via: str) -> None:
    item = acc.get(key) or acc.setdefault(key, make())
    item.score += 1.0 / (RRF_K + rank)
    item.via.append(via)


def _from_fields(hit, via: str) -> Retrieved:
    f = hit.fields
    return Retrieved(
        id=hit.id,
        source_type=f.get("source_type") or "",
        authority=f.get("authority") or "",
        ref=f.get("ref") or "",
        title=f.get("title") or "",
        heading=f.get("heading") or "",
        text=f.get("chunk_text") or "",
        doc_id=f.get("doc_id") or "",
        char_start=f.get("char_start"),
        char_end=f.get("char_end"),
        via=[via],
    )


__all__ = ["Chunk", "NS_GUIDANCE", "NS_LAW", "RagLayer", "Retrieved", "org_namespace"]
