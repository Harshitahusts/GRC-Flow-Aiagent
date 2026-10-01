"""Hybrid retrieval: BM25 (exact legal terms) fused with vector search (meaning).

Legal text needs both. "Section 8(6)" or "Data Fiduciary" must match exactly, which
BM25 does well; "who must be told about a leak" has no shared words with the
provision, which embeddings handle. The two ranked lists are merged with
reciprocal rank fusion (RRF), which needs no score calibration between them.

Every hit is resolved back to the BM25 corpus chunk, so the text the agent sees is
always the ingested gazette text, never whatever a vector record happens to hold.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from grc_agent.corpus.ingest import Chunk
from grc_agent.corpus.store import Corpus

from grc_flow.adapters.vectors import VectorIndex

RRF_K = 60


@dataclass
class Retrieved:
    ref: str
    heading: str
    text: str
    score: float
    via: list[str] = field(default_factory=list)


class HybridRetriever:
    def __init__(self, corpus: Corpus, index: VectorIndex, version: str) -> None:
        self.corpus = corpus
        self.index = index
        self.version = version
        self._by_key = {c.key: c for c in corpus.chunks}

    def search(self, query: str, k: int = 5) -> list[Retrieved]:
        fused: dict[str, Retrieved] = {}

        def add(chunk: Chunk, rank: int, via: str) -> None:
            item = fused.get(chunk.key)
            if item is None:
                item = fused[chunk.key] = Retrieved(chunk.ref, chunk.heading, chunk.text, 0.0)
            item.score += 1.0 / (RRF_K + rank)
            item.via.append(via)

        for rank, hit in enumerate(self.corpus.search(query, k * 2), start=1):
            add(hit.chunk, rank, "bm25")
        for rank, hit in enumerate(self.index.search(query, k * 2), start=1):
            if hit.fields.get("corpus_version") not in (None, self.version):
                continue  # stale record from an older corpus build
            chunk = self._by_key.get(hit.id)
            if chunk is not None:
                add(chunk, rank, "vector")

        ranked = sorted(fused.values(), key=lambda r: -r.score)[:k]
        for r in ranked:
            r.score = round(r.score, 5)
        return ranked

    def provision(self, ref: str) -> list[Chunk]:
        return self.corpus.provision(ref)

    def resolves(self, ref: str) -> bool:
        return self.corpus.index.resolves(ref)
