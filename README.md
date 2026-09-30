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
| [docs/research/03-training-datasets.md](docs/research/03-training-datasets.md) | Kaggle and related datasets for training and evaluation, plus the DPDPA eval sets we must build |
| [THIRD_PARTY.md](THIRD_PARTY.md) | Licence register for every external project we reference or depend on |

## Status

Research and architecture (Phase 0 planning). No production code yet.

> Not legal advice. Compliance outputs must be reviewed by qualified counsel.
