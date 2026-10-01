"""Vector index for the DPDPA corpus: Pinecone in production, in-memory locally.

Pinecone runs with *integrated embedding*: the index embeds `chunk_text` itself
(model set once at index creation), so we send text, not vectors, and need no
separate embedding provider. Records carry the citation and heading as fields so
a hit can be cited without a second lookup.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from grc_agent.corpus.store import tokenize

TEXT_FIELD = "chunk_text"
FIELDS = [TEXT_FIELD, "ref", "heading", "kind", "source", "corpus_version"]
UPSERT_BATCH = 90  # integrated-embedding upserts are capped at 96 records per request


@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    fields: dict[str, Any]


class VectorIndex(Protocol):
    name: str

    def ensure(self) -> None: ...
    def upsert(self, records: list[dict[str, Any]]) -> int: ...
    def search(self, text: str, top_k: int = 5) -> list[VectorHit]: ...
    def count(self) -> int | None: ...


class MemoryIndex:
    """TF-IDF cosine over the same records. Stand-in for tests and offline work."""

    name = "memory"

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self._dirty = True

    def ensure(self) -> None:
        return None

    def upsert(self, records: list[dict[str, Any]]) -> int:
        for r in records:
            self.records[r["_id"]] = dict(r)
        self._dirty = True
        return len(records)

    def _build(self) -> None:
        docs = {rid: Counter(tokenize(r[TEXT_FIELD])) for rid, r in self.records.items()}
        n = max(len(docs), 1)
        df = Counter(t for tf in docs.values() for t in tf)
        self._idf = {t: math.log((1 + n) / (1 + f)) + 1 for t, f in df.items()}
        self._vecs = {rid: self._vec(tf) for rid, tf in docs.items()}
        self._dirty = False

    def _vec(self, tf: Counter) -> dict[str, float]:
        v = {t: c * self._idf.get(t, 0.0) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def search(self, text: str, top_k: int = 5) -> list[VectorHit]:
        if self._dirty:
            self._build()
        q = self._vec(Counter(tokenize(text)))
        scored = [
            (sum(w * vec.get(t, 0.0) for t, w in q.items()), rid) for rid, vec in self._vecs.items()
        ]
        scored = sorted((s for s in scored if s[0] > 0), reverse=True)[:top_k]
        return [
            VectorHit(rid, round(score, 4), {k: self.records[rid].get(k) for k in FIELDS})
            for score, rid in scored
        ]

    def count(self) -> int | None:
        return len(self.records)


class PineconeIndex:
    name = "pinecone"

    def __init__(
        self,
        api_key: str,
        index_name: str,
        namespace: str,
        cloud: str,
        region: str,
        embed_model: str,
    ) -> None:
        from pinecone import Pinecone

        self._pc = Pinecone(api_key=api_key)
        self.index_name = index_name
        self.namespace = namespace
        self.cloud = cloud
        self.region = region
        self.embed_model = embed_model
        self._index = None

    def ensure(self) -> None:
        """Create the index (integrated embedding on chunk_text) if it doesn't exist."""
        if not self._pc.indexes.exists(self.index_name):
            self._pc.indexes.create_for_model(
                name=self.index_name,
                cloud=self.cloud,
                region=self.region,
                embed={"model": self.embed_model, "field_map": {"text": TEXT_FIELD}},
                timeout=300,
            )
        self._index = self._pc.index(name=self.index_name)

    @property
    def index(self):
        if self._index is None:
            self._index = self._pc.index(name=self.index_name)
        return self._index

    def upsert(self, records: list[dict[str, Any]]) -> int:
        sent = 0
        for i in range(0, len(records), UPSERT_BATCH):
            batch = records[i : i + UPSERT_BATCH]
            self.index.upsert_records(namespace=self.namespace, records=batch)
            sent += len(batch)
        return sent

    def search(self, text: str, top_k: int = 5) -> list[VectorHit]:
        response = self.index.search(
            namespace=self.namespace, top_k=top_k, inputs={"text": text}, fields=FIELDS
        )
        return [VectorHit(h.id, float(h.score), dict(h.fields)) for h in response.result.hits]

    def count(self) -> int | None:
        stats = self.index.describe_index_stats()
        ns = (getattr(stats, "namespaces", None) or {}).get(self.namespace)
        return getattr(ns, "vector_count", None) if ns is not None else 0


def make_index(settings) -> VectorIndex:
    if not settings.pinecone_api_key:
        return MemoryIndex()
    return PineconeIndex(
        api_key=settings.pinecone_api_key,
        index_name=settings.pinecone_index,
        namespace=settings.pinecone_namespace,
        cloud=settings.pinecone_cloud,
        region=settings.pinecone_region,
        embed_model=settings.pinecone_embed_model,
    )
