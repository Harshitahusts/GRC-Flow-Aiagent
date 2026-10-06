"""Building the law corpus with GRC-Ai's ingester.

GRC-Ai's ingester (`grc_agent.corpus.ingest`) parses the Act and Rules listed in
`<corpus>/manifest.json` into citable provisions ("Section 8(6)", "Rule 7(2)") and
writes `build/chunks.jsonl` + `build/corpus_index.txt`. Numbering gaps fail the
build: a corpus with a missing section would let the agent cite around the hole.
Indexing the result into Pinecone is the RAG layer's job (grc_flow.rag.layer).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from grc_agent.corpus.ingest import ingest
from grc_agent.corpus.store import Corpus, load_corpus


class RagInitError(RuntimeError):
    pass


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


def load_built_corpus(corpus_dir: str | Path) -> tuple[Corpus, str] | None:
    """The already-built corpus (no re-ingest), or None if `rag-init` hasn't run."""
    build = Path(corpus_dir) / "build"
    corpus = load_corpus(build)
    return (corpus, corpus_version(build)) if corpus else None
