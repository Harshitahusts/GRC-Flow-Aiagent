# DPDPA corpus

The only citable source for the agent: the gazette texts, parsed by GRC-Ai's ingester.

| File | What | Ingested |
|---|---|---|
| `dpdp_act_2023.pdf` | Digital Personal Data Protection Act, 2023 (No. 22 of 2023), Government of India Press print, 11 Aug 2023 | Sections 1–44 + Schedule, no gaps |
| `dpdp_rules_2025.pdf` | DPDP Rules, 2025, G.S.R. 846(E), 13 Nov 2025, English pages | Rules 1–23 + Schedules 1–7, no gaps |

**Provenance (to fix):** meity.gov.in and egazette.gov.in were blocked from the environment that
built this, so both files are copies taken from a public GitHub repo (see `manifest.json`). The
Act is the unmodified Government of India Press PDF. The Rules file is the English half of the
bilingual gazette, re-saved by a third party. Download both from MeitY / eGazette, replace the files,
update the `sha256` values in `manifest.json`, and re-run `grc-flow rag-init`.
`rag-init` refuses any file whose hash doesn't match the manifest.

```bash
grc-flow rag-init                                   # ingest, chunk, index (law + guidance)
python scripts/eval_retrieval.py corpus/questions.draft.json
```

`questions.draft.json` is GRC-Ai's **draft** retrieval set (10 questions). Current score, local
index: 8/10 in the top 3. Both misses return a defensible Rule (Rule 3 for the notice, Rule 8 for
erasure) that the draft doesn't accept yet. Review the expected provisions, then measure again on Pinecone.

Known parsing gaps: the Act's PDF doesn't give the parser the section headings (the Rules have
them), and a few words keep stray spaces from the PDF line breaks ("seventy -two"). Neither affects
citations; both are fixes for GRC-Ai's ingester.
