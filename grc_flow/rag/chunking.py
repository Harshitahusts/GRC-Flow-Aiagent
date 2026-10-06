"""Chunking for every knowledge source, sized for the embedding model.

Why it matters: Pinecone's integrated embedding *truncates* input longer than the
model's limit (multilingual-e5-large: ~507 tokens), so an oversized chunk is
silently embedded on its first part only and the rest can never be found. Every
chunk here is kept under a token budget, measured with a conservative estimate.

How text is split, coarse to fine, so a chunk breaks at the most natural place:
  law        one provision per chunk; long provisions split at clause lines
  guidance   one heading section per chunk; long sections split at paragraphs
  register   one obligation per chunk (always small)
  documents  paragraphs packed into windows, with a sentence or two of overlap
Paragraphs that are too long fall back to sentences (including the Devanagari
danda "।"), and sentences that are too long fall back to words.

Each chunk keeps exact character offsets into its source text
(`text[char_start:char_end] == body`), so a hit can be traced back to the
document, and the chunk text starts with a short header (title > heading) so a
piece read on its own still says where it came from.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from grc_flow.adapters.vectors import TEXT_FIELD

MAX_TOKENS = 380  # under e5-large's ~507, leaving room for the header and its "passage:" prefix
OVERLAP_TOKENS = 40

_PARAGRAPH = re.compile(r"\S(?:.*?\S)?(?=[ \t]*\n[ \t]*\n|\s*\Z)", re.DOTALL)
_LINE = re.compile(r"[^\n]*\S[^\n]*")
# A sentence ends at . ! ? or । followed by whitespace, so "3.4" and "8(6)." mid-text stay whole.
_SENTENCE = re.compile(r"\S.*?(?:[.!?।]+(?=\s)|$)", re.DOTALL)
_WORD = re.compile(r"\S+")
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_HEADING = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.MULTILINE)


def estimate_tokens(text: str) -> int:
    """Upper-bound-ish token count without a tokenizer: the larger of a
    characters-based and a words-based estimate (Indic scripts take more tokens
    per word, English more per character)."""
    if not text:
        return 0
    return max(math.ceil(len(text) / 4), math.ceil(len(text.split()) * 1.35))


@dataclass(frozen=True)
class Chunk:
    id: str
    source_type: str  # law | guidance | register | document
    authority: str  # law (citable) | guidance | org
    header: str
    body: str
    char_start: int
    char_end: int
    chunk_index: int
    ref: str = ""  # law citation, or obligation id
    doc_id: str = ""
    title: str = ""
    heading: str = ""

    @property
    def text(self) -> str:
        return f"{self.header}\n{self.body}" if self.header else self.body

    def to_record(self, version: str) -> dict:
        return {
            "_id": self.id,
            TEXT_FIELD: self.text,
            "source_type": self.source_type,
            "authority": self.authority,
            "ref": self.ref,
            "heading": self.heading,
            "doc_id": self.doc_id,
            "title": self.title,
            "chunk_index": self.chunk_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "version": version,
        }


# Splitting -----------------------------------------------------------------------------


def _spans(pattern: re.Pattern, text: str, start: int, end: int) -> list[tuple[int, int]]:
    out = []
    for m in pattern.finditer(text, start, end):
        s, e = m.start(), m.end()
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e))
    return out


def _units(text: str, budget: int, block: re.Pattern) -> list[tuple[int, int, bool]]:
    """Sentence-sized units, each within budget, flagged True when it ends a block
    (paragraph, or line for law), which is where a chunk would best end."""
    out: list[tuple[int, int, bool]] = []
    for bs, be in _spans(block, text, 0, len(text)):
        pieces: list[tuple[int, int]] = []
        for ss, se in _spans(_SENTENCE, text, bs, be):
            if estimate_tokens(text[ss:se]) <= budget:
                pieces.append((ss, se))
                continue
            words = _spans(_WORD, text, ss, se)
            i = 0
            while i < len(words):  # hard split a run-on sentence by words
                j = i
                while j + 1 < len(words) and (
                    estimate_tokens(text[words[i][0] : words[j + 1][1]]) <= budget
                ):
                    j += 1
                pieces.append((words[i][0], words[j][1]))
                i = j + 1
        out += [(ps, pe, n == len(pieces) - 1) for n, (ps, pe) in enumerate(pieces)]
    return out


def split(
    text: str,
    budget: int = MAX_TOKENS,
    overlap: int = OVERLAP_TOKENS,
    by: str = "paragraph",
) -> list[tuple[int, int]]:
    """Character spans of `text`, each within `budget` tokens.

    Sentences are packed greedily; a window that would end mid-paragraph is pulled
    back to the last paragraph end if that keeps at least half the budget. Each
    window after the first starts with the previous window's trailing sentences
    (up to `overlap` tokens), so a fact cut at a boundary is still whole somewhere.
    """
    units = _units(text, budget - overlap, _LINE if by == "line" else _PARAGRAPH)
    windows: list[tuple[int, int]] = []
    i, s0 = 0, None
    while i < len(units):
        start = units[i][0] if s0 is None else s0
        j = i
        while j + 1 < len(units) and estimate_tokens(text[start : units[j + 1][1]]) <= budget:
            j += 1
        if j + 1 < len(units) and not units[j][2]:
            for k in range(j - 1, i - 1, -1):
                if units[k][2] and estimate_tokens(text[start : units[k][1]]) >= budget // 2:
                    j = k
                    break
        windows.append((start, units[j][1]))
        # Overlap: trailing sentences of this window, never the whole window.
        k = j + 1
        while k - 1 > i and estimate_tokens(text[units[k - 1][0] : units[j][1]]) <= overlap:
            k -= 1
        s0 = units[k][0] if k <= j else None
        i = j + 1
    return windows


# Source shapers ------------------------------------------------------------------------


def safe_id(value: str) -> str:
    """Pinecone ids are ASCII; keep readable ids and hash anything else."""
    clean = re.sub(r"[^A-Za-z0-9_.:()\-]+", "_", value.strip()).strip("_")
    if clean and clean.isascii() and len(clean) <= 120:
        return clean
    return (clean[:60] + "_" if clean else "") + hashlib.sha1(value.encode()).hexdigest()[:12]


def chunk_law(provisions: Iterable, budget: int = MAX_TOKENS) -> list[Chunk]:
    """Corpus chunks from GRC-Ai's ingester (one per section/sub-section)."""
    out = []
    for p in provisions:
        header = f"{p.ref}. {p.heading}".strip()
        spans = split(p.text, budget - estimate_tokens(header), OVERLAP_TOKENS, by="line")
        for i, (s, e) in enumerate(spans):
            out.append(
                Chunk(
                    id=f"law:{safe_id(p.key)}#{i}",
                    source_type="law",
                    authority="law",
                    header=header,
                    body=p.text[s:e],
                    char_start=s,
                    char_end=e,
                    chunk_index=i,
                    ref=p.ref,
                    heading=p.heading,
                    title=p.source,
                )
            )
    return out


def chunk_markdown(doc_id: str, markdown: str, budget: int = MAX_TOKENS) -> list[Chunk]:
    """Guidance notes: front matter title, then one section per heading."""
    title, body_start = doc_id, 0
    fm = _FRONT_MATTER.match(markdown)
    if fm:
        body_start = fm.end()
        m = re.search(r"^title:\s*(.+)$", fm.group(1), re.MULTILINE)
        title = m.group(1).strip() if m else doc_id
    heads = [(m.start(), m.end(), m.group(1)) for m in _HEADING.finditer(markdown, body_start)]
    sections = []
    cursor, heading = body_start, ""
    for hs, he, name in heads:
        sections.append((cursor, hs, heading))
        cursor, heading = he, name
    sections.append((cursor, len(markdown), heading))

    out = []
    for s0, e0, heading in sections:
        header = f"{title} > {heading}" if heading else title
        section = markdown[s0:e0]
        for s, e in split(section, budget - estimate_tokens(header)):
            out.append(
                Chunk(
                    id=f"guide:{safe_id(doc_id)}#{len(out)}",
                    source_type="guidance",
                    authority="guidance",
                    header=header,
                    body=section[s:e],
                    char_start=s0 + s,
                    char_end=s0 + e,
                    chunk_index=len(out),
                    doc_id=doc_id,
                    title=title,
                    heading=heading,
                )
            )
    return out


def chunk_register(obligations: Iterable) -> list[Chunk]:
    """One chunk per obligation in GRC-Ai's register."""
    out = []
    for i, o in enumerate(obligations):
        body = (
            f"Obligation: {o.obligation}\nEvidence to request: {o.evidence}\n"
            f"Remediation: {o.remediation}"
        )
        out.append(
            Chunk(
                id=f"oblig:{safe_id(o.id)}",
                source_type="register",
                authority="guidance",
                header=f"{o.id} ({o.severity}) under {o.source}",
                body=body,
                char_start=0,
                char_end=len(body),
                chunk_index=i,
                ref=o.source,
                doc_id=o.id,
                title="DPDPA obligations register",
            )
        )
    return out


def chunk_document(
    doc_id: str, text: str, title: str = "", budget: int = MAX_TOKENS
) -> list[Chunk]:
    """An organisation's own document (privacy notice, policy, contract...)."""
    header = title or doc_id
    prefix = f"doc:{safe_id(doc_id)}#"
    return [
        Chunk(
            id=f"{prefix}{i}",
            source_type="document",
            authority="org",
            header=header,
            body=text[s:e],
            char_start=s,
            char_end=e,
            chunk_index=i,
            doc_id=doc_id,
            title=title,
        )
        for i, (s, e) in enumerate(split(text, budget - estimate_tokens(header)))
    ]


def doc_prefix(doc_id: str) -> str:
    return f"doc:{safe_id(doc_id)}#"
