"""Vector store: one Pinecone index, one namespace per knowledge source and per org.

Pinecone runs with *integrated embedding*: the index embeds `chunk_text` itself
(the model is chosen once, at index creation), so we send text, not vectors, and
need no separate embedding provider.

Namespaces (see grc_flow.rag.layer):
  law        the DPDP Act and Rules (authoritative, citable)
  guidance   analyst knowledge: obligation register and guidance notes (not citable)
  org-<id>   one tenant's own documents; a query never crosses into another org

`MemoryIndex` is a TF-IDF stand-in with the same interface for tests and offline work.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from grc_agent.corpus.store import tokenize

TEXT_FIELD = "chunk_text"
FIELDS = [
    TEXT_FIELD,
    "source_type",
    "authority",
    "ref",
    "heading",
    "doc_id",
    "title",
    "chunk_index",
    "char_start",
    "char_end",
    "version",
]
UPSERT_BATCH = 90  # integrated-embedding upserts are capped at 96 records per request
DELETE_BATCH = 1000


@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    fields: dict[str, Any]


class VectorIndex(Protocol):
    name: str

    def ensure(self) -> dict[str, Any]: ...
    def upsert(self, namespace: str, records: list[dict[str, Any]]) -> int: ...
    def search(self, namespace: str, text: str, top_k: int = 5) -> list[VectorHit]: ...
    def list_ids(self, namespace: str, prefix: str) -> list[str]: ...
    def delete_ids(self, namespace: str, ids: list[str]) -> int: ...
    def count(self, namespace: str) -> int | None: ...


class MemoryIndex:
    name = "memory"

    def __init__(self) -> None:
        self.namespaces: dict[str, dict[str, dict[str, Any]]] = {}
        self._cache: dict[str, tuple[dict[str, float], dict[str, dict[str, float]]]] = {}

    def ensure(self) -> dict[str, Any]:
        return {"index": "memory", "max_tokens": None}

    def upsert(self, namespace: str, records: list[dict[str, Any]]) -> int:
        ns = self.namespaces.setdefault(namespace, {})
        for r in records:
            ns[r["_id"]] = dict(r)
        self._cache.pop(namespace, None)
        return len(records)

    def _model(self, namespace: str):
        if namespace not in self._cache:
            ns = self.namespaces.get(namespace, {})
            docs = {rid: Counter(tokenize(r[TEXT_FIELD])) for rid, r in ns.items()}
            n = max(len(docs), 1)
            df = Counter(t for tf in docs.values() for t in tf)
            idf = {t: math.log((1 + n) / (1 + f)) + 1 for t, f in df.items()}
            self._cache[namespace] = (idf, {rid: _unit(tf, idf) for rid, tf in docs.items()})
        return self._cache[namespace]

    def search(self, namespace: str, text: str, top_k: int = 5) -> list[VectorHit]:
        idf, vecs = self._model(namespace)
        q = _unit(Counter(tokenize(text)), idf)
        scored = sorted(
            ((sum(w * v.get(t, 0.0) for t, w in q.items()), rid) for rid, v in vecs.items()),
            reverse=True,
        )
        ns = self.namespaces.get(namespace, {})
        return [
            VectorHit(rid, round(s, 4), {k: ns[rid].get(k) for k in FIELDS})
            for s, rid in scored[:top_k]
            if s > 0
        ]

    def list_ids(self, namespace: str, prefix: str) -> list[str]:
        return [i for i in self.namespaces.get(namespace, {}) if i.startswith(prefix)]

    def delete_ids(self, namespace: str, ids: list[str]) -> int:
        ns = self.namespaces.get(namespace, {})
        gone = [ns.pop(i) for i in ids if i in ns]
        self._cache.pop(namespace, None)
        return len(gone)

    def count(self, namespace: str) -> int | None:
        return len(self.namespaces.get(namespace, {}))


def _unit(tf: Counter, idf: dict[str, float]) -> dict[str, float]:
    v = {t: c * idf.get(t, 0.0) for t, c in tf.items()}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


class PineconeIndex:
    name = "pinecone"

    def __init__(
        self, api_key: str, index_name: str, cloud: str, region: str, embed_model: str
    ) -> None:
        from pinecone import Pinecone

        self._pc = Pinecone(api_key=api_key)
        self.index_name = index_name
        self.cloud = cloud
        self.region = region
        self.embed_model = embed_model
        self._index = None

    def ensure(self) -> dict[str, Any]:
        """Create the index if missing, and report the embedding model's input limit,
        so rag-init can refuse chunks the model would silently truncate."""
        if not self._pc.indexes.exists(self.index_name):
            self._pc.indexes.create_for_model(
                name=self.index_name,
                cloud=self.cloud,
                region=self.region,
                embed={"model": self.embed_model, "field_map": {"text": TEXT_FIELD}},
                timeout=300,
            )
        self._index = self._pc.index(name=self.index_name)
        info = self._pc.inference.model.get(self.embed_model)
        return {"index": self.index_name, "max_tokens": info.max_sequence_length}

    @property
    def index(self):
        if self._index is None:
            self._index = self._pc.index(name=self.index_name)
        return self._index

    def upsert(self, namespace: str, records: list[dict[str, Any]]) -> int:
        sent = 0
        for i in range(0, len(records), UPSERT_BATCH):
            batch = records[i : i + UPSERT_BATCH]
            self.index.upsert_records(namespace=namespace, records=batch)
            sent += len(batch)
        return sent

    def search(self, namespace: str, text: str, top_k: int = 5) -> list[VectorHit]:
        response = self.index.search(
            namespace=namespace, top_k=top_k, inputs={"text": text}, fields=FIELDS
        )
        return [VectorHit(h.id, float(h.score), dict(h.fields)) for h in response.result.hits]

    def list_ids(self, namespace: str, prefix: str) -> list[str]:
        return [
            item.id
            for page in self.index.list(prefix=prefix, namespace=namespace)
            for item in page.vectors
            if item.id
        ]

    def delete_ids(self, namespace: str, ids: list[str]) -> int:
        for i in range(0, len(ids), DELETE_BATCH):
            self.index.delete(ids=ids[i : i + DELETE_BATCH], namespace=namespace)
        return len(ids)

    def count(self, namespace: str) -> int | None:
        stats = self.index.describe_index_stats()
        ns = (getattr(stats, "namespaces", None) or {}).get(namespace)
        return getattr(ns, "vector_count", 0) if ns is not None else 0


def make_index(settings) -> VectorIndex:
    if not settings.pinecone_api_key:
        return MemoryIndex()
    return PineconeIndex(
        api_key=settings.pinecone_api_key,
        index_name=settings.pinecone_index,
        cloud=settings.pinecone_cloud,
        region=settings.pinecone_region,
        embed_model=settings.pinecone_embed_model,
    )
