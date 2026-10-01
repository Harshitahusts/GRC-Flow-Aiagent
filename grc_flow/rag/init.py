"""RAG init: ingest the gazette texts, then load the chunks into the vector index.

1. GRC-Ai's ingester (`grc_agent.corpus.ingest`) parses the Act and Rules listed in
   `<corpus>/manifest.json` into citable chunks ("Section 8(6)", "Rule 7(2)") and
   writes `build/chunks.jsonl` + `build/corpus_index.txt`. Numbering gaps fail the
   init: a corpus with a missing section would let the agent cite around the hole.
2. Every chunk becomes one record in the vector index, keyed by its canonical
   citation, with the text embedded by the index itself (Pinecone integrated
   embedding). Re-running is safe: same ids, so records are overwritten.
3. The corpus version (sha256 of chunks.jsonl) is stamped on every record. The
   retriever ignores hits from another version, and the checker reports drift.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from grc_agent.corpus.ingest import ingest
from grc_agent.corpus.store import Corpus, load_corpus

from grc_flow.adapters.vectors import TEXT_FIELD, VectorIndex


class RagInitError(RuntimeError):
    pass


@dataclass(frozen=True)
class RagInitReport:
    corpus_version: str
    chunks: int
    upserted: int
    index: str
    sources: list[str]


def corpus_version(build_dir: Path) -> str:
    return hashlib.sha256((build_dir / "chunks.jsonl").read_bytes()).hexdigest()[:16]


def build_corpus(corpus_dir: str | Path) -> tuple[Corpus, str]:
    corpus_dir = Path(corpus_dir)
    report = ingest(corpus_dir)
    if not report.ok:
        problems = [
            f"{s.file}: missing {', '.join(s.missing)}" for s in report.sources if s.missing
        ]
        problems += [f"unresolved chunk {r}" for r in report.unresolved_chunks[:5]]
        raise RagInitError(
            "The corpus didn't ingest cleanly; fix it before indexing. "
            + ("; ".join(problems) or "No chunks were found.")
        )
    corpus = load_corpus(corpus_dir / "build")
    assert corpus is not None  # ingest just wrote it
    return corpus, corpus_version(corpus_dir / "build")


def to_records(corpus: Corpus, version: str) -> list[dict]:
    return [
        {
            "_id": chunk.key,
            TEXT_FIELD: f"{chunk.ref}. {chunk.heading}\n{chunk.text}",
            "ref": chunk.ref,
            "heading": chunk.heading,
            "kind": chunk.kind,
            "source": chunk.source,
            "corpus_version": version,
        }
        for chunk in corpus.chunks
    ]


def init_rag(corpus_dir: str | Path, index: VectorIndex) -> tuple[Corpus, RagInitReport]:
    corpus, version = build_corpus(corpus_dir)
    index.ensure()
    upserted = index.upsert(to_records(corpus, version))
    return corpus, RagInitReport(
        corpus_version=version,
        chunks=len(corpus.chunks),
        upserted=upserted,
        index=index.name,
        sources=sorted({c.source for c in corpus.chunks}),
    )


def load_built_corpus(corpus_dir: str | Path) -> tuple[Corpus, str] | None:
    """The already-built corpus (no re-ingest), or None if `rag-init` hasn't run."""
    build = Path(corpus_dir) / "build"
    corpus = load_corpus(build)
    return (corpus, corpus_version(build)) if corpus else None
