# GRC-Flow AI Agent

**An AI DPDPA Compliance Analyst that lives inside your GRC platform.**

GRC-Flow isn't a DPDPA dashboard with a chatbot attached. The AI agent is the primary
compliance analyst. It observes the organisation's GRC data, investigates issues, recommends and
drafts fixes, acts once a person approves, verifies the result, and remembers decisions. The
dashboard is how the agent works and communicates.

```
Observe → Understand → Investigate → Recommend → Act → Verify → Remember
```

Core principle: **the LLM extracts facts, deterministic rules decide compliance.** Every finding
shows the source provision, the evidence it checked, its reasoning, a confidence score, and needs
human approval before anything changes.

## Documentation

| Doc | What's in it |
|-----|--------------|
| [docs/research/01-reference-repos-study.md](docs/research/01-reference-repos-study.md) | Study of 11 open-source DPDPA / RAG compliance projects: architecture, flaws, licences, what to reuse |
| [docs/architecture/02-agent-blueprint.md](docs/architecture/02-agent-blueprint.md) | Agent architecture, data model, finding schema, memory design, deployment modes, roadmap |
| [docs/architecture/04-system-design.md](docs/architecture/04-system-design.md) | **Prototype system design**: stack, flows, checker agent, data model, security, deployment, GRC-Ai integration |
| [docs/research/03-training-datasets.md](docs/research/03-training-datasets.md) | Kaggle and related datasets for training and evaluation, plus the DPDPA eval sets we must build |
| [THIRD_PARTY.md](THIRD_PARTY.md) | Licence register for every external project we reference or depend on |

## Prototype: the backend pipeline

`grc_flow/` is the backend AI pipeline for the [GRC-Ai](https://github.com/Harshitahusts/GRC-Ai)
website. It reuses GRC-Ai's corpus ingester, BM25 search, citation index and Postgres layer.

```
event (website / n8n) → Upstash queue → worker:
  retrieve law (BM25 + Pinecone) → Claude extracts facts with quotes → checker grounds quotes
  → dpdp-law-to-code decides → agent memory → checker verifies → Supabase → n8n
```

Stack: Supabase (Postgres), Pinecone, Upstash Redis, Clerk, Sentry, n8n, Cloudflare, GitHub.
Every service has a local stand-in, so it runs with no keys:

```bash
pip install -e ".[dev]"
grc-flow demo          # offline end to end on a fictional corpus
pytest -q              # 42 tests; set GRC_FLOW_TEST_DATABASE_URL to run them on Postgres
```

With real services: copy `.env.example` to `.env`, put the gazette PDFs and `manifest.json` in
`corpus/`, then `grc-flow rag-init`, `grc-flow check`, `grc-flow canary`, and
`docker compose up -d` (API, worker, n8n). Import the workflows in `n8n/workflows/`.

| Command | What it does |
|---|---|
| `grc-flow rag-init` | Ingest the Act and Rules, create and fill the Pinecone index |
| `grc-flow serve [--with-worker]` | API on :8080 (`--with-worker` for single-box setups) |
| `grc-flow worker [--once]` | Process queued events |
| `grc-flow check` / `canary` | Checker agent: health probes / known-answer runs |
| `grc-flow submit FILE` | Queue an event from a JSON file |

## Status

A working prototype (see the status table in the system design). It is tested offline and on
PostgreSQL. It has not yet run against live Pinecone, Upstash, Clerk, Sentry or Claude accounts.

> Not legal advice. Compliance outputs must be reviewed by qualified counsel.
