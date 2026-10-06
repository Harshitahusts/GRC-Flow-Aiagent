"""Retrieval eval for the law namespace: does the expected provision come back in the top k?

Usage: python scripts/eval_retrieval.py [corpus/questions.draft.json]

A hit is a retrieved provision that equals an expected one or sits inside it
("Section 8(6)" for "Section 8"). Uses the configured index (Pinecone when
PINECONE_API_KEY is set and rag-init has run, else the local stand-in).
The questions file is GRC-Ai's DRAFT set: review every expected provision
against the gazette before trusting the score (target: 9/10 in the top 3).
"""

from __future__ import annotations

import json
import sys

from grc_agent.kpis.citations import normalize_citation

from grc_flow.config import Settings
from grc_flow.runtime import Runtime


def hit(got: list[str], expected: list[str]) -> bool:
    exp = [normalize_citation(e) for e in expected]
    return any(
        g and e and (g == e or g.startswith(e + "("))
        for g in (normalize_citation(x) for x in got)
        for e in exp
    )


def main(path: str = "corpus/questions.draft.json") -> int:
    questions = json.load(open(path, encoding="utf-8"))
    rt = Runtime.build(Settings.from_env())
    if rt.rag is None:
        print("No knowledge base. Run `grc-flow rag-init` first.", file=sys.stderr)
        return 1
    print(f"index: {rt.index.name}  knowledge version: {rt.rag.version}")
    for k in (1, 3, 5):
        bm25 = sum(
            hit([h.chunk.ref for h in rt.rag.corpus.search(q["question"], k)], q["expected"])
            for q in questions
        )
        hybrid = sum(
            hit([h.ref for h in rt.rag.search(q["question"], k, ("law",))], q["expected"])
            for q in questions
        )
        print(f"top {k}: BM25 {bm25}/{len(questions)}   hybrid {hybrid}/{len(questions)}")
    for q in questions:
        got = [h.ref for h in rt.rag.search(q["question"], 3, ("law",))]
        if not hit(got, q["expected"]):
            print(f"  miss: {q['question']}  expected {q['expected']}  got {got}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
