"""The knowledge the analyst and the agent start with. Deliberately small.

  law        DPDP Act 2023 + DPDP Rules 2025, from the gazette PDFs in corpus/
             (the only citable source)
  register   GRC-Ai's DPDPA obligations register: obligation, evidence to request,
             remediation per provision (13 rows; GRC-Ai marks it a sample to review)
  guidance   GRC-Ai's 13 DPDPA guidance pages (~5,300 words): notice, consent,
             rights, breach, children, SDF, cross-border, penalties, timeline...

Register and guidance ship inside the `grc-agent` package (MIT, same team), so
there's nothing to download. They are indexed as `authority: guidance`: the
analyst may use them to explain and to plan, but findings can only cite `law`.
More sources (DPB orders, MeitY FAQs, sector rules) plug in here later.
"""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass

from grc_agent.register import load_register

from grc_flow.rag.chunking import Chunk, chunk_markdown, chunk_register


@dataclass(frozen=True)
class GuidanceDoc:
    doc_id: str
    markdown: str


def guidance_docs() -> list[GuidanceDoc]:
    folder = importlib.resources.files("grc_agent.content_seed") / "docs"
    return [
        GuidanceDoc(p.name.removesuffix(".md"), p.read_text("utf-8"))
        for p in sorted(folder.iterdir(), key=lambda p: p.name)
        if p.name.endswith(".md")
    ]


def guidance_chunks() -> list[Chunk]:
    chunks = chunk_register(load_register().obligations)
    for doc in guidance_docs():
        chunks += chunk_markdown(doc.doc_id, doc.markdown)
    return chunks
