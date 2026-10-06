# The RAG layer

One layer (`grc_flow/rag/`) holds everything the agent and the analyst know, in one Pinecone
index, and is the only way the pipelines and the analyst retrieve it. The data is deliberately
small for now.

## What's in it

| Namespace | Contents | Size today | Authority | Updated by |
|---|---|---|---|---|
| `law` | DPDP Act 2023 + DPDP Rules 2025, one or more chunks per provision | 198 provisions → 232 chunks (14 long provisions split) | **law: the only citable source** | `grc-flow rag-init` |
| `guidance` | GRC-Ai's obligations register (obligation, evidence to request, remediation) + GRC-Ai's 13 DPDPA guidance pages | 13 register rows + 78 guidance chunks (~5,300 words) | guidance: explains and plans, never cited | `grc-flow rag-init` |
| `org-<clerk org id>` | each organisation's own documents (notices, policies...) | grows with use | org: the client's own words | the pipeline (`index_document` step), `DELETE /v1/documents/{id}` |

Register and guidance ship inside the `grc-agent` package (MIT, same team), so there's nothing to
download. GRC-Ai marks the register as a sample to review against the gazette; until that review
is done, treat guidance as drafting help, not authority.

## How text is chunked (`rag/chunking.py`)

Pinecone's integrated embedding **truncates** input past the model's limit
(`multilingual-e5-large`: ~507 tokens), and the cut-off part can never be found. So every chunk
stays under **380 estimated tokens** (header included). The estimate is conservative: the larger
of characters/4 and words×1.35, which also covers Hindi. `rag-init` asks Pinecone for the model's
real limit and refuses to index anything over it.

| Source | Split | Header on every chunk |
|---|---|---|
| Law | one chunk per provision; long ones at clause lines `(a)`, `(i)` | `Section 8(6). General obligations` |
| Guidance pages | one chunk per `##` section; long ones at paragraphs | `DPDPA notice requirements > Common mistakes` |
| Register | one chunk per obligation | `OBL-007 (high) under Section 8(6)` |
| Org documents | paragraphs packed into windows | the document title |

The splitter is shared:
- it works in sentences (ending at `.` `!` `?` `।` followed by a space, so `3.4` and `8(6).` stay
  whole), with words as a last resort;
- it prefers to end a chunk at a paragraph (or clause line) as long as that keeps at least half the budget;
- it repeats up to 40 tokens of trailing sentences at the start of the next chunk, so a fact cut at a
  boundary is still whole somewhere.

Every chunk keeps exact character offsets (`text[char_start:char_end] == body`), so a hit points
back to the precise place in the source.

## How it's searched (`rag/layer.py`)

```
query ─┬─ law:      GRC-Ai BM25 (exact terms) ┐ RRF → one law list (full provision text)
       │            Pinecone `law`            ┘
       ├─ guidance: Pinecone `guidance`  ─────── one list
       └─ org:      Pinecone `org-<id>`  ─────── one list (only with the caller's org id)
                         └── RRF across the lists (one vote per source) → top k
```

- BM25 and vectors are fused *inside* law first. Before this, law got two votes and crowded
  guidance out of mixed answers.
- Law hits return the full ingested provision, so quotes and citations are gazette text.
- Records carry the knowledge version. Hits from an older `rag-init` are ignored, and the checker's
  health probe reports the namespace as stale.
- `org` search needs the org id. The API takes it from the Clerk token and the analyst tools bind
  it when they're built, so a model or a client can't choose another org.

## Who uses it

| Caller | Scopes | How |
|---|---|---|
| Notice pipeline | indexes the notice into `org-<id>`, retrieves `law` (sent to Claude as context) and `guidance` (recorded on the run) | `index_document`, `retrieve_knowledge` steps |
| GRC-Ai analyst agent | `law`, `guidance`, `org` | `knowledge_tools(rt.rag, org_id)` adds `search_knowledge` and `get_law_provision` to GRC-Ai's `Agent` |
| Website | any | `POST /v1/rag/search {query, scope}`, `DELETE /v1/documents/{id}` |
| Checker | all | health probe: record counts vs expected, version match per namespace |

## Commands

```bash
grc-flow rag-init    # ingest corpus/, chunk law + guidance, create the index, sync, drop stale records
grc-flow check       # health, including the per-namespace counts and versions
```

## Next (when we add data)

1. Replace the corpus PDFs with direct MeitY/eGazette downloads (see `corpus/README.md`), review
   `corpus/questions.draft.json`, and score retrieval on Pinecone (`scripts/eval_retrieval.py`).
   Local index today: 8/10 in the top 3.
2. Review the register against the gazette, then let findings attach the matching register row
   (evidence to request, remediation).
3. More sources as their own namespaces with their own authority: DPB orders, MeitY FAQs, sector rules.
4. A Pinecone-hosted reranker on the fused list, if the retrieval eval shows it helps.
