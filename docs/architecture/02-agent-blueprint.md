# GRC-Flow: AI DPDPA Compliance Analyst (Agent Blueprint v0.1)

> *"Your always-on AI DPDPA Compliance Officer"*, not "a chatbot for DPDPA".
> The agent is the primary analyst. The dashboard is how the agent observes, acts, and communicates.

This blueprint turns the product vision and the [reference-repo study](../research/01-reference-repos-study.md)
into a buildable architecture. Where we borrow an idea, the source repo is named.

---

## 1. Product loop

```
 Observe ─► Understand ─► Investigate ─► Recommend ─► Act (with approval) ─► Verify ─► Remember
    ▲                                                                                   │
    └───────────────────────────────────────────────────────────────────────────────────┘
```

Five capabilities map onto the loop:

| Capability | Loop stage | Mostly deterministic or LLM? |
|------------|-----------|-------------------------------|
| 👀 **Compliance Observer** | Observe | Deterministic (change-data-capture, schedulers, webhooks) |
| 🧠 **DPDPA Reasoning Engine** | Understand | **Deterministic rules decide.** LLM extracts facts |
| 🔎 **Compliance Investigator** | Investigate | LLM plans; tools fetch evidence; memory de-duplicates |
| 🛠️ **Compliance Consultant** | Recommend / Act | LLM drafts; human approves; system applies |
| 🤖 **Continuous Compliance Agent** | Verify / Remember | Scheduler + memory + re-run rules |

---

## 2. Non-negotiable design principles

These come straight from the failures observed in the study.

1. **LLM → facts, rules → verdict.** The LLM never emits PASS/FAIL. It fills typed *fact records*,
   and each field carries evidence references and a confidence score. A deterministic rule engine (built on
   `dpdp-law-to-code` + our own Rules-2025 layer) returns the verdict and citation.
   *(Avoids GuardPulse/GovernAI/privacyanalyzer prompt-verdicts.)*
2. **Fail closed.** If a citation can't be verified against the corpus, or a fact has low confidence, the
   finding becomes `needs_human_review`. It is never silently marked valid. *(Fixes GuardPulse Critic and the Rexskth validator.)*
3. **Authoritative corpus only.** The knowledge base contains only the gazette text of the DPDP Act 2023 and the
   DPDP Rules 2025 (plus later notifications), and each chunk is version-stamped (`effective_date`, `last_verified`,
   `source_url`, `sha256`). Commentary is labelled `commentary` and can never be cited as law.
   *(Avoids the fabricated retention classes and "sensitive data" errors we found.)*
4. **Show your work.** Every finding carries: requirement → rule → facts → evidence checked → reasoning →
   confidence → proposed action.
5. **Human-in-the-loop for anything that changes an artifact.** The agent proposes and a person approves. Every
   action is written to a hash-chained audit ledger *(TSI CMS design)*.
6. **Memory is first-class.** Accepted risks, dismissals and org context suppress or annotate repeat findings,
   until the underlying facts change.
7. **Provider-agnostic model layer.** The same agent runs on a hosted LLM (SaaS) or a customer-hosted model (private).

---

## 3. System architecture

```
                         ┌────────────────────────────────────────────────┐
                         │                 AGENT RUNTIME                  │
                         │  Planner/Orchestrator (typed state machine)   │
                         │   ├─ Observer      ├─ Investigator            │
                         │   ├─ Reasoner      ├─ Consultant (drafts)     │
                         │   └─ Verifier (critic, fails closed)          │
                         └───────┬──────────────┬───────────────┬────────┘
                                 │ tools (MCP)  │               │
            ┌────────────────────┼──────────────┼───────────────┼────────────────────┐
            ▼                    ▼              ▼               ▼                    ▼
   ┌─────────────────┐ ┌──────────────────┐ ┌───────────────┐ ┌────────────────┐ ┌──────────────┐
   │ DPDPA Knowledge │ │  Rule Engine     │ │  Org Memory   │ │ GRC Data Layer │ │ Action Layer │
   │ Base (RAG)      │ │ dpdp-law-to-code │ │ (facts,       │ │ (entities,     │ │ tasks, drafts│
   │ Act+Rules only, │ │ + Rules-2025     │ │ decisions,    │ │ evidence,      │ │ approvals,   │
   │ section-chunked,│ │ layer + our      │ │ history,      │ │ controls)      │ │ notifications│
   │ hybrid search   │ │ obligation reg.  │ │ embeddings)   │ │                │ │              │
   └─────────────────┘ └──────────────────┘ └───────────────┘ └───────┬────────┘ └──────────────┘
                                                                      │
                          ┌───────────────────────────────────────────┼─────────────────────┐
                          ▼                     ▼                     ▼                     ▼
                   Connectors: TSI DPDP CMS · document stores (Drive/SharePoint) · HRMS/CRM
                   · vendor lists · code repos (scanner) · manual upload
                                                                      │
                                                                      ▼
                                           ┌──────────────────────────────────────┐
                                           │ GRC Dashboard: "AI Analyst" home,    │
                                           │ Findings, Actions, Evidence, Chat    │
                                           └──────────────────────────────────────┘
```

### 3.1 DPDPA Knowledge Base (RAG)
- **Corpus:** Act 2023, Rules 2025 (all Schedules), and later notifications. Store the PDFs with a hash and the gazette reference.
- **Chunking:** statute-aware, one chunk per Section/Rule with sub-section and clause splits when large.
  De-duplicate the table of contents by keeping the last match. Merge tiny sections. *(GuardPulse `chunker.py` approach.)*
  Chunk IDs are canonical, e.g. `DPDPA-2023:S8(6)`, `DPDPR-2025:R7(2)`, `DPDPR-2025:SCH3`.
- **Metadata:** `instrument, section, subsection, clause, title, effective_date, phase_in_date, last_verified,
  source_url, sha256, status(active|superseded)`. *(Extends Rexskth's chunk metadata.)*
- **Retrieval:** hybrid BM25 + dense (start at 0.3/0.7, tune on an eval set). Exact-citation lookup bypasses search
  (`get_provision("S8(6)")`). *(Rexskth hybrid retriever.)*
- **Obligation register:** our own structured catalogue built from the corpus. Each obligation has
  `id, citation(s), text, obligation_type, applies_when (profile predicate), penalty_category,
  evidence_expected[], rule_fn, phase_in_date`. *(Shape inspired by GovernAI JSON and Tushar's `requirements` table.
  Content authored by us from the gazette, and legally reviewed.)*

### 3.2 Rule Engine
- **Core:** `dpdp-law-to-code` (MIT) as a pinned dependency for Act §§5–16.
- **Our Rules-2025 layer** in the same style (`ComplianceResult` in, citation out):
  Rule 3 notice contents, Rule 6 safeguards, Rule 7 breach (without-delay intimation + 72h report), Rule 8 + Third Schedule
  retention/erasure and 48h pre-erasure notice, Rule 8(3) 1-year log retention, Rule 10 verifiable parental consent,
  Rule 13 SDF, Rule 14 rights. Check every rule against the gazette with a lawyer before release.
- **Applicability predicates:** profile → which obligations are in scope (universal / Third-Schedule class / children /
  SDF / Consent Manager / cross-border). *(Tushar's `requirement_mappings` idea.)*
- **Fact wrapper:** we don't pass bare booleans. Each input is a `Fact{value, evidence_refs[], confidence, source:
  rule|llm|human, asserted_at}`. The engine evaluates on `value`. The finding reports the weakest-link confidence.
- Fix upstream heuristics we disagree with (e.g. the 72h-to-principals breach timer) in our wrapper, and send a PR upstream.

### 3.3 Org Memory
| Layer | Contents | Store |
|-------|----------|-------|
| **Organisation profile** | industry, entity class, user counts, SDF status, locations, children's data, cross-border | relational |
| **Knowledge graph** | Processing Activity ↔ Data Category ↔ Data Principal type ↔ Purpose ↔ Legal basis ↔ System ↔ Vendor ↔ Policy/Notice ↔ Evidence ↔ Control | relational (graph-shaped tables); graph DB later if needed |
| **Decision memory** | accepted risks, dismissals, overrides, *each with rationale, approver, scope, expiry, and the fact-hash it was based on* | relational |
| **Finding history** | every finding version, state transitions, who/when | append-only |
| **Semantic memory** | embeddings of policies, notices, prior conversations for recall | pgvector |

**Suppression rule:** a dismissed or accepted finding stays suppressed **only while the fact-hash it was decided on is
unchanged and the decision hasn't expired**. When the facts change, the finding resurfaces with "previously accepted
by X on D because R. The situation has changed: …". This answers the request that the agent "remembers our legal
team approved this" without hiding new risk.

### 3.4 GRC Data Layer (core entities)
`Organization, ProcessingActivity (RoPA), DataCategory, DataPrincipalType, Purpose, LegalBasis (consent | S7(a)…S7(i)),
System, Vendor/Processor, ProcessorContract, Notice/Policy (versioned), ConsentRecord/ConsentPolicy, RightsRequest,
Grievance, BreachIncident, Evidence (with expiry), Control, Obligation, Finding, Action/Task, Approval, AuditLog`.

`ProcessingActivity` follows the TSI CMS `ropa_entries` field set, with `legal_basis` mapped to DPDPA terms
(no GDPR `vital_interest`/`legal_obligation`; use §7 sub-clauses instead).

### 3.5 Finding schema (the agent's main output)
```jsonc
{
  "id": "FND-2026-0142",
  "title": "Employee privacy notice omits purpose for 3 data categories",
  "obligation_ids": ["OBL-NOTICE-001"],
  "citations": ["DPDPA-2023:S5(1)(i)", "DPDPR-2025:R3(b)"],   // verified against corpus
  "rule_result": { "fn": "dpdp.notice.check_notice", "compliant": false, "sub_results": [...] },
  "facts": [
    { "name": "describes_purpose", "value": false, "confidence": 0.91, "source": "llm",
      "evidence_refs": ["doc:emp-notice-v4#sec4", "ropa:PA-017", "ropa:PA-021", "ropa:PA-022"] }
  ],
  "affected": { "processing_activities": ["PA-017","PA-021","PA-022"] },
  "evidence_checked": 7,
  "severity": "medium",               // rule-derived + penalty category, never LLM-only
  "penalty_category": "general",      // Schedule category for exposure roll-up
  "confidence": 0.87,                 // min(fact confidences) × citation-verification
  "status": "open",                   // open | needs_human_review | accepted_risk | dismissed | in_remediation | resolved | verified
  "memory": { "similar_prior": ["FND-2025-0098"], "prior_decision": null },
  "recommendation": { "summary": "Update §4 of the employee privacy notice", "draft_ref": "draft:DRF-311",
                      "owner_role": "Privacy/Legal", "due": "2026-10-15" },
  "false_positive_note": "Purposes may be stated in a linked annexure; check before approving."
}
```
*(`false_positive_note` + `confidence` pattern from compliance-copilot's rule schema.)*

### 3.6 Agent runtime
- **Typed state machine, not free-form chat.** Plain Python orchestration with Pydantic models. GuardPulse shows this
  stays transparent. A durable workflow engine (e.g. Temporal, or a Postgres-backed job queue) is needed for
  long-running investigations. *(GovernAI's in-memory job dict is what not to do.)*
- **Investigation plan** (per trigger): scope → applicability → gather facts (tools) → run rules → check memory →
  verify citations → score → draft recommendation → route.
- **Verifier (critic):** checks (a) every cited provision exists in the corpus and the quoted text matches by hash/substring;
  (b) every fact has at least one evidence ref that resolves; (c) confidence ≥ threshold. **Any failure → `needs_human_review`.**
- **Tools exposed via MCP** so the same toolset serves the in-app agent, external assistants, and private deployments
  *(dpdpa-mcp pattern)*: `get_provision`, `search_law`, `list_obligations(profile)`, `run_rule`, `query_grc(entity, filter)`,
  `get_evidence`, `detect_indian_pii` (Aadhaar with Verhoeff, PAN, mobile), `scan_repo` (compliance-copilot-style rules),
  `recall_memory`, `create_draft`, `create_task`, `request_approval`.
- **Streaming stages** to the UI (`observing → investigating → checking rules → verifying → drafting`) *(Rexskth UX)*.

### 3.7 Observer (continuous monitoring)
Triggers:
- **Change events** from the GRC data layer (new vendor, changed notice version, new processing activity, edited RoPA).
- **Connector webhooks**, e.g. TSI CMS HMAC-signed webhooks + polling reconciliation for consent, grievance, purge and breach events.
- **Schedules**: evidence expiry (T-30/T-7), grievance SLA timers, breach clocks, Third-Schedule 3-year inactivity erasure
  with the 48h pre-notice, periodic re-assessment, and phase-in dates (e.g. obligations commencing May 2027).
- **Law updates**: a new notification lands in the corpus → re-evaluate affected obligations for every org.

Each trigger starts an investigation. Its results go into the **daily brief** ("Good afternoon. 🔴 2 issues require attention…").

### 3.8 Consultant (remediation)
- Drafts redlines for notices and policies from the org's *current* artifact plus the failing sub-results. Shows a diff.
- **[Review] [Approve] [Edit] [Ask agent] [Reject]**. Reject/dismiss requires a reason, which goes into decision memory.
- On approve: create a new artifact version, link evidence, re-run the rules (**Verify**), and close only if they now pass.
- Document templates are **our own**, drafted with counsel. *(We do not copy Tushar's DOCX templates, which are licence-restricted.)*

### 3.9 Scoring and exposure
- Per-obligation status comes from the rules. Domain roll-ups follow an 8-domain maturity view (Notice, Consent, Security,
  Breach, Retention/Erasure, Children, Rights/Grievance, Governance/SDF).
- **Exposure by Schedule penalty category.** If any open gap exists in a category, show the category cap as the *maximum*
  exposure, clearly labelled as a statutory ceiling and not an estimate.
- **Hard caps:** an open critical security or breach finding caps the headline score *(GuardPulse rule)*.

---

## 4. Deployment modes

| Mode | Where data lives | Model | Notes |
|------|------------------|-------|-------|
| **A. SaaS** | Our cloud (India region) | Hosted API under a DPA with no-training terms | Fastest onboarding |
| **B. Private** | Customer infra (Docker/K8s) | Customer-hosted open-weight model or their own enterprise endpoint | Same containers; model adapter swapped by config |
| **C. Hybrid** | Sensitive docs + extraction on customer side | Local model for PII-bearing extraction; central for reasoning on redacted facts | Private "edge agent" ships **facts + evidence hashes**, not raw documents, to the central dashboard |

Hybrid works cleanly because of principle #1: the rule engine only needs **facts**, so raw personal data never
has to leave the customer's environment.

---

## 5. Suggested stack (initial)

| Concern | Choice | Why |
|---------|--------|-----|
| API / agent runtime | Python 3.12, FastAPI, Pydantic v2 | Matches `dpdp-law-to-code`; the study repos converge here |
| DB | PostgreSQL + pgvector | One store for entities, memory, vectors; runs in private deployments |
| Lexical search | Postgres FTS or OpenSearch (BM25) | Hybrid retrieval |
| Jobs / workflows | Postgres-backed queue to start; Temporal when investigations get long | Durable, restart-safe |
| Tools protocol | MCP server | Reuse across in-app agent, IDE assistants, private installs |
| LLM adapter | Thin provider interface (hosted API / OpenAI-compatible local endpoint) | Deployment modes A/B/C |
| Frontend | Next.js dashboard | "AI Analyst" home, findings, approvals, chat |
| Audit | Hash-chained append-only `audit_log` | Tamper-evidence (TSI design) |

---

## 6. Build roadmap

**Phase 0: Foundations (knowledge you can trust)**
- Ingest Act + Rules → statute-aware chunks with canonical IDs → hybrid retrieval → `get_provision` / `search_law`.
- Obligation register v1 (authored from the gazette, reviewed by counsel), with applicability predicates.
- Wrap `dpdp-law-to-code` and add the Rules-2025 layer. Build an eval set of fact patterns → expected verdicts.
- Retrieval/citation eval set: questions → gold provisions. Track recall@k and citation accuracy.

**Phase 1: Analyst MVP (single org, on-demand)**
- Core entities + RoPA + notices/policies + evidence upload.
- Investigator for 4 high-value checks: notice completeness (§5/R3), consent validity (§6), breach readiness (§8(6)/R7),
  retention/erasure (§8(7)/R8/Third Schedule).
- Finding schema, fail-closed verifier, Consultant drafts with approve/reject, audit ledger.
- Chat over the org's own data ("Why do we have 2 issues?", "What should we fix first?").

**Phase 2: Memory and continuous mode**
- Decision memory with fact-hash suppression. Finding history and de-duplication.
- Observer: change events, schedules, evidence expiry, daily brief.
- TSI DPDP CMS connector (API + webhooks + polling).

**Phase 3: Breadth and deployment**
- Vendor/processor assessments, children's data (§9/R10), SDF (§10/R13), cross-border (§16), rights SLAs.
- Indian PII detector + repo scanner tools. MCP server published.
- Private and hybrid deployment packaging.

---

## 7. Open questions for the team
1. Which customer segment first (startups, mid-market, SDF-likely enterprises)? This decides which obligations we build first.
2. Legal reviewer for the obligation register and Rules layer: in-house or a partner firm?
3. Default hosted model and region for SaaS. Which open-weight model do we certify for private mode?
4. Do we contribute our Rules-2025 layer back upstream to `dpdp-law-to-code` (MIT) or keep it proprietary?
