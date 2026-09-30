# Study: Open-Source DPDPA / RAG Compliance Agents on GitHub

**Purpose:** Before building the GRC-Flow AI DPDPA Compliance Analyst, we cloned and read every
repository surfaced in our research. This document records what each one does, how it works, what
it gets wrong, whether we are allowed to reuse it, and what we should take from it.

**Method:** Shallow-cloned all 11 repos (September 2026 heads), read the READMEs and the
core source (agents, retrieval, rule logic, schemas), and ran the test suite where one existed.
Line counts and file paths below refer to those heads.

> **Bottom line:** None of these is the product we are building. Each solves one slice
> (a rule engine, a RAG chatbot, a consent system, a code scanner, a gap questionnaire).
> Our differentiator is the layer none of them have: a **stateful agent with org memory**
> that continuously watches GRC data, investigates, proposes fixes, and learns from
> human decisions.

---

## 1. Summary matrix

| # | Repo | What it really is | Stack | License (as found in repo) | Reuse verdict |
|---|------|-------------------|-------|-----------------------------|---------------|
| 1 | [Wolfgangrush/dpdp-law-to-code](https://github.com/Wolfgangrush/dpdp-law-to-code) | Deterministic DPDP Act §5–16 checks as pure Python functions | Python stdlib only | **MIT** (LICENSE file) | ✅ **Adopt as a dependency** (rule engine core) |
| 2 | [AnakinSkywalker-0/guardpulse-connect](https://github.com/AnakinSkywalker-0/guardpulse-connect) | 3-agent RAG document auditor (Auditor / Tech / Critic) with a scoring formula | FastAPI, ChromaDB, sentence-transformers, Groq/Gemini, n8n | ❌ **No LICENSE file** (all rights reserved by default) | 📖 Ideas only, do not copy code |
| 3 | [Rexskth/Indian_compliance_multi_agent_RAG](https://github.com/Rexskth/Indian_compliance_multi_agent_RAG) | Q&A chatbot over DPDPA / IT Act / Companies Act with hybrid retrieval | FastAPI, ChromaDB, rank-bm25, OpenRouter, Next.js | ⚠️ README badge says MIT, **no LICENSE file** | 📖 Ideas only |
| 4 | [sharadshriram/dpdpa-mcp](https://github.com/sharadshriram/dpdpa-mcp) | MCP server: Indian PII detection (Aadhaar Verhoeff, PAN) and code linting for consent/retention | Python, FastMCP | ⚠️ README badge says MIT, **no LICENSE file** | 📖 Ideas only (Verhoeff is public-domain maths; re-implement) |
| 5 | [Tushar-9802/DPDPA](https://github.com/Tushar-9802/DPDPA) | Questionnaire-driven gap assessment: 46 obligations, penalty exposure, maturity model, 7 DOCX templates | Python, Streamlit, SQLite | ❌ **Source-available, commercial use prohibited** | 🚫 Study the design only. **Do not copy code, data or templates** |
| 6 | [RushikeshSonwane03/GovernAI](https://github.com/RushikeshSonwane03/GovernAI) | Multi-framework (DPDP / GDPR / EU AI Act / NIST) RAG document scorer with async jobs | FastAPI, LangChain LCEL, FAISS, Ollama/Gemini, React | ❌ **No LICENSE file** | 📖 Ideas only |
| 7 | [privacypriority/privacyanalyzer](https://github.com/privacypriority/privacyanalyzer) | Public website privacy-policy scorer (LLM prompt, dual score) | Next.js 15, OpenRouter | **Apache-2.0** | ⚠️ Reusable, but **contains legal errors** (see §8) |
| 8 | [tsi-coop/tsi-dpdp-cms](https://github.com/tsi-coop/tsi-dpdp-cms) | Production-style self-hosted **Consent Management System**: RoPA, consent API, grievances, breach, hash-chained audit ledger | Java (Jetty), PostgreSQL, pgcrypto | **Apache-2.0** | ✅ **Integrate as a data source / connector**; borrow schema ideas |
| 9 | [ThomasMoreAI/legal-skills-open](https://github.com/ThomasMoreAI/legal-skills-open) (`in/data-protection/skills/*`) | Two DPDPA "skills" (long expert system prompts) | Markdown | Repo **Apache-2.0**; the individual skill files declare **MIT** from their upstream authors | ✅ Usable as prompt reference, with attribution |
| 10 | [ESR-style/compliance-copilot](https://github.com/ESR-style/compliance-copilot) | Agent Skills pack (5 frameworks) + regex code scanner with a JSON rule format | TypeScript, Markdown | **MIT** | ✅ Borrow rule JSON format and skill structure |
| 11 | [yogeshwarbari/dpdp-quick-audit-vercel-deploy](https://github.com/yogeshwarbari/dpdp-quick-audit-vercel-deploy) | Toy repo scanner: 10 regexes over a concatenated repo, score = 100 − penalties | FastAPI, Next.js | **MIT** | ❌ Too crude to be useful |

**Legal note:** "No LICENSE file" means default copyright applies: we may read the code and learn
from the ideas, but must not copy it into a commercial product. A README badge is not a licence grant.
Have counsel confirm before shipping anything derived from repos 2–6.

---

## 2. Wolfgangrush/dpdp-law-to-code: the rule engine ⭐ most valuable

**What it is.** A `pip install`-able library (`dpdp-law-to-code`) with **94 public `check_*`/`assess_*` functions**
covering DPDP Act Sections 5–16. Each function takes a frozen dataclass of *facts* and returns
a `ComplianceResult(compliant, section, reason, citation, sub_results)`.

**Verified:** `pytest` → **407 passed in 0.67s**. No runtime dependencies.

**Module map (`dpdp/`):**

| Module | Covers | Example entry point |
|--------|--------|---------------------|
| `notice.py` | §5 notice + §5(2) legacy notice | `check_notice(NoticeRecord)` |
| `consent.py` | §6: free/specific/informed/unconditional/unambiguous, withdrawal, consent managers, burden of proof | `check_consent(ConsentRecord)` |
| `legitimate.py` | §7(a)–(i) legitimate uses | `check_legitimate_use(LegitimateUseRecord)` |
| `fiduciary.py` | §8: accountability, processor contracts, accuracy, security safeguards, breach notification, erasure, DPO contact, grievance | `check_fiduciary_compliance(...)` |
| `children.py` | §9: verifiable parental consent, tracking/ad prohibitions, exemptions | `check_child_processing(...)` |
| `sdf.py` | §10 Significant Data Fiduciary: DPO, independent auditor, DPIA | `check_sdf_obligations(SDFContext)` |
| `rights.py` | §11–14: access, correction/erasure, grievance, nomination | `check_sec_11/12/13/14(...)` |
| `duties.py` | §15 Data Principal duties | `check_data_principal_duty(...)` |
| `cross_border.py` | §16 negative-list + sectoral law | `check_cross_border_transfer(...)` |

**Why it matters for us.** This is the concrete version of the rule we set for ourselves:
*the LLM must not decide compliance on its own.* The agent's job becomes:

```
GRC data ──► LLM extracts/infers FACTS (with evidence refs) ──► dpdp-law-to-code decides ──► verdict + citation
```

The verdict is reproducible and testable, and it cites a section. The LLM only fills in the
fact dataclasses, and every field it fills can be traced to evidence.

**Gaps and cautions:**
- Only Act §§5–16. There is no DPDP Rules 2025 layer (Rule 3 notice contents, Rule 6 safeguards list,
  Rule 7 breach report, Rule 8 + Third Schedule retention, Rule 10 VPC, etc.). **We must add a Rules layer.**
- Breach timing is a heuristic: it applies **72h to both the Board and Data Principals**
  (`fiduciary.py:19-20`, where the comment says it was written against the *Draft* Rules). The notified Rules require
  intimation *without delay* and a detailed report to the Board within 72h. **Re-calibrate this before we rely on it.**
- Inputs are booleans (e.g. `is_informed: bool`). That is fine for a harness, but our agent needs to
  attach **evidence + confidence per boolean**. Wrap it; don't fork it.

---

## 3. GuardPulse Connect: the closest *agent architecture*

**Pipeline** (`orchestrator.py`): `AuditorAgent → TechArchitectAgent → CriticAgent → score → exec summary → n8n`.

- **Section-aware legal chunking** (`chunker.py`): detects `^\d+[A-Z]?\.\s+Title` headers,
  de-duplicates Table-of-Contents hits by keeping the *last* occurrence, merges sections under 200 chars,
  and splits sections over 3,200 chars at `(1)`/`(a)` clause markers. **Good idea, we should adopt it.**
- **Auditor** (`agents/auditor_agent.py`): a hardcoded list of 8 DPDP clauses (§5, 6, 8, 9, 11, 12, 13, 16).
  For each law it runs fixed RAG queries against Chroma, then a single LLM call returns a JSON array of
  `{clause_ref, verdict PASS/FAIL/PARTIAL/INCONCLUSIVE, evidence, confidence}`.
- **Critic** (`agents/critic_agent.py`): re-retrieves law text per clause and asks the LLM whether the
  claim is supported. Hallucination rate feeds the score.
- **Score**: `0.50·legal + 0.35·tech + 0.15·(100·(1−hallucination_rate))`, **hard-capped at 55**
  if any CRITICAL security finding exists.
- Pydantic v2 models for every agent output (`models.py`). Plain-Python orchestration, no framework.

**Problems we must not repeat:**
1. **The Critic fails open.** On empty retrieval, parse error, or `confidence < 0.75` it marks the
   citation **valid** (`critic_agent.py` `_verify`). It even says "if the law text is about a different
   topic, set is_valid to true". A verifier that defaults to "valid" verifies nothing.
2. **The prompts decide the verdict.** "Mark §13 PASS if any 'contact us' exists" and "§9 PASS if children
   are mentioned even to say the service is not for them" are legal judgements hidden in prompts.
3. Documents are truncated to the first **3,000 chars** (`doc[:3000]`), so long policies are never actually read.
4. Only 8 clauses. No Rules. No memory, no org context, runs once per upload.
5. Hygiene: merge-conflict markers are committed in `requirements.txt`, the repo contains `.env` and uploaded files, and there is no licence.

**Take:** the Auditor → Critic *shape*, section-aware chunking, strict Pydantic outputs, and the
"critical finding caps the score" rule. **Change:** make the critic fail **closed** and move verdicts into the rule engine.

---

## 4. Indian_compliance_multi_agent_RAG: the *retrieval* reference

**Pipeline** (`backend/agents/orchestrator.py`): two-layer cache → keyword intent classifier
(`risk` / `compliance` / `legal` / `general`) → `LegalAgent` (hybrid retrieve top-7 + synthesize) →
`RiskAgent` (only for risk/compliance intents) → `CitationValidator` → answer + citations + risk level.
Streams stage events to the UI (`searching → analyzing → risk_assessment → validating → generating`).

- **Hybrid retrieval** (`retrieval/hybrid_retriever.py`): vector (Chroma) + BM25, each max-normalised,
  combined `0.7·vec + 0.3·bm25`, top-k. **Good default. BM25 matters for legal text** because exact tokens
  like "Section 8(6)" or "Data Fiduciary" carry the meaning.
- Chunk metadata carries `source, document_name, section_number, page_number, effective_date,
  last_verified, status`. **Versioning metadata on legal chunks is the right idea.**
- The streaming stage UX works well in a dashboard.

**Weaknesses:**
- The chunker is **sentence/page-based** (512 "tokens" ≈ words×1.3), not section-aware, so `section_number` is usually empty.
- Intent is decided by keyword `if`s.
- `CitationValidator` regex-extracts "Section N" and asks an LLM over only the **first 2,000 chars** of context.
  If no citations are found it returns `is_valid=True, confidence=1.0`.
- Default LLM is a 1.2B free model. The README makes marketing claims ("zero hallucination",
  and cited "real incidents" we could not verify). **Do not reuse any README facts.**

**Take:** the hybrid retriever, chunk metadata schema, stage streaming, and response cache keyed on normalised query.

---

## 5. dpdpa-mcp: the *tool* pattern (MCP)

Three MCP tools in `server.py` (FastMCP):

| Tool | What it does | Quality |
|------|--------------|---------|
| `identify_bharat_pii` | Aadhaar (12 digits + **Verhoeff D5 checksum**, `verhoeff.py`), PAN (`[A-Z]{3}[PCHFABTLJG][A-Z]\d{4}[A-Z]`), email, +91 mobile. Normalises Devanagari/Bengali/Gujarati digits. "Aggressive" sliding-window mode | **Good.** The checksum removes most false positives |
| `audit_privacy_compliance` | Regex lint: infinite TTL (`ttl=-1`, `datetime.max`), personal data without a consent-gate keyword, `getattr(user,"consent")` bypass | Heuristic |
| `get_dpdpa_requirement` | Downloads the Act + Rules PDFs and **keyword-filters paragraphs** | Not real RAG |

The metrics logger writes only entity *labels* to JSONL, never PII values. That is a good privacy-by-design pattern for our telemetry too.

**Legal error to avoid:** the README maps Aadhaar to "DPDPA Schedule (Sensitive PII)". **The DPDP Act has
no sensitive-personal-data category.** Aadhaar handling is governed by the Aadhaar Act and UIDAI regulations
(sectoral overlay), not a DPDPA "schedule".

**Take:** expose our agent's capabilities as **MCP tools** so the same tools serve the in-product agent,
Claude/Cursor users, and private deployments. Re-implement Verhoeff ourselves (it's a published algorithm).

---

## 6. Tushar-9802/DPDPA: the *GRC data model* reference (look, don't copy)

> **Licence: Source-Available with Commercial Restrictions.** Explicitly forbids "Integration into paid
> compliance software" and "SaaS company offering DPDP assessments based on this code". Commercial
> licence is priced at ₹50k–₹5L+. **We must not copy its code, its 46-row requirements seed, or its DOCX templates.**
> We can learn from its *structure*, which is generic GRC practice.

**Design worth learning from:**
- `requirements` table: `rule_number, section_number, requirement_text, obligation_type
  (notice/consent/security/breach/retention/rights/children/sdf…), deadline, penalty_category_id, is_sdf_specific`.
- `requirement_mappings(requirement_id, trigger_condition, priority_weight)`: **applicability is driven by the
  business profile** (universal / Third-Schedule class / children / SDF). Our "is this obligation even in scope?"
  step needs exactly this.
- `schedule_references`: Third Schedule classes (e-commerce ≥2 crore users, social media ≥2 crore,
  online gaming ≥50 lakh → 3-year erasure from last approach). This matches the notified Rules.
- Penalty exposure is grouped by **Schedule category cap** (₹250cr security, ₹200cr breach notification,
  ₹200cr children, ₹150cr SDF, ₹50cr residual, ₹10k Data Principal duties). Exposure = cap if any gap exists in that category.
- **8-domain, 5-level maturity model** (Notice, Consent, Security, Breach, Retention, Children, Rights/Grievance, Governance/SDF).
- Registers: consent register, breach register, data mapping.

**Take (as ideas, with our own implementation and our own obligation register built from the official texts):**
profile-driven applicability, penalty-category exposure, maturity domains, registers.

---

## 7. GovernAI: the *multi-framework async analysis* reference

- `frameworks/dpdp_obligations.json`: **68 structured obligations**
  `{act, section, title, obligation, obligation_type, applies_to[], keywords[]}` across §§1–37. It is a nice shape
  for an obligation catalogue, but the licence is unclear, so rebuild it from the gazette text.
- LCEL chain per framework: `retriever → format_docs → prompt | llm | JsonOutputParser`. Each section scores 1 / 0.5 / 0,
  with an overall framework score.
- **Async job pattern:** `POST /analyze-async → job_id`, `GET /analyze-status/{job_id}`. The job store is an in-memory dict, which is lost on restart.
- **Governance questionnaire** runs while the document is analysed. That is good UX for filling org context.
- SQLite `audit_logs(event, document_id, report_id, details, source)`.
- It commits `data/uploads/*`, `rai_compliance.db` and the FAISS index to git.

**Take:** obligation-catalogue JSON shape, async-job + questionnaire UX, per-event audit log. **Fix:** a durable job queue.

---

## 8. privacyanalyzer: a cautionary tale about LLM-authored law

A polished Next.js app that sends a website's privacy policy to an LLM with a long rubric prompt
(`src/app/api/analyze/route.ts`, 1,051 lines) and returns an "Overall Privacy Score" plus a "DPDP Compliance Score" out of 10.

**It contains confident legal errors**, which is exactly why our knowledge base must be built only from the official texts:
- `docs/regulations/DPDP_RULES_2025_INTEGRATION.md` claims retention "Class A: 3 years, Class B: 10 years,
  Class C: indefinite". **This does not exist in the Rules.** The Third Schedule names specific classes
  (large e-commerce, online gaming and social media intermediaries) with a 3-year period.
- The prompt says "Sensitive personal data handling: special protections per Sec. 9". §9 is about **children**, and
  DPDPA has no sensitive-data class.

**Take:** the dual-audience idea (a user-facing score vs a business-facing compliance score), OpenRouter
key rotation and fallbacks, rate limiting, and caching per analysed domain. **Never** take its legal content.

---

## 9. TSI DPDP CMS: the *consent system of record* (integrate, don't rebuild)

The most production-grade repo in the set: Java/Jetty + PostgreSQL, Apache-2.0, OpenAPI spec, JMeter load tests,
regression test catalogue, HMAC-signed webhooks, and a polling fallback.

**Schema highlights (`db/*.sql`):**
- `ropa_entries`: `activity_name, purpose, legal_basis, data_categories[], data_subject_categories[],
  retention_period_days, retention_start_event (COLLECTION|CESSATION), processors[], cross_border_transfers[],
  security_measures, dpo_id, linked_policy_ids[], status (draft|active|under_review|retired), version`
  + `ropa_history` snapshots. **This is almost exactly our "Processing Activity" entity.**
- `consent_records` linked to `ropa_entry_id`, plus `consent_validations` (runtime purpose checks by processors).
- `grievances`, `purge_requests`, `breach_incidents`, `breach_affected_principals`, `parental_verification_logs`.
- **Tamper-evident audit ledger:** `audit_logs.prev_log_hash / current_log_hash / digital_signature` and
  `evidence_certificates` for Bharatiya Sakshya Adhiniyam (BSA) §63-style electronic evidence.
- Column-level PII encryption via pgcrypto + HMAC lookup columns.

**Caveat:** `legal_basis` enum includes `legal_obligation` and `vital_interest`. These are GDPR terms. Under DPDPA
those situations fall under **§7 legitimate uses** (e.g. §7(d)/(e) legal obligation or court orders, §7(f) medical emergency).
Our mapping layer must translate.

**Take:** (1) a **TSI CMS connector** as one of our first integrations. Our Observer reads RoPA, consent,
grievance, purge and breach data through its API and webhooks. (2) Adopt the RoPA field set and the hash-chained audit ledger design.

---

## 10. legal-skills-open DPDPA skills and compliance-copilot: the *knowledge-prompt* layer

**legal-skills-open** (`in/data-protection/skills/ind-dpdpa-expert`, `.../dpdpa`, ~400 lines each):
long expert system prompts that cover roles, §3 scope, §§5–17, Rules themes, SDF, Consent Manager,
sectoral overlay (RBI/SEBI/IRDAI/CERT-In/ABDM), GDPR comparison, **evidence patterns by obligation**,
implementation pitfalls, and a roadmap. `ind-dpdpa-expert` also references an SCF (Secure Controls Framework)
crosswalk: *41 SCF controls → 96 DPDPA control IDs*. That is useful for mapping to ISO 27001/SOC 2 later.
Some statements are GDPR-flavoured (e.g. fee rules for repeat requests), so **verify each one before using it**.

**compliance-copilot** (MIT):
- `skills/dpdp/SKILL.md` follows the Agent Skills format (frontmatter `name`, `description`, `metadata.last_verified`,
  `law_version`), with a clear "DPDP vs GDPR" table (2 lawful bases, age 18, no legitimate interests, digital-only).
- `skills/dpdp/scan-rules.json` has a **rule schema worth copying**:
  `{id, name, severity, type (absent_in_file…), obligation, citation, description, remediation, patterns[],
  trigger_patterns[], applicable_extensions[], confidence, false_positive_note}`.
  The `false_positive_note` + `confidence` pair is exactly what an analyst-style finding needs.
- `docs/architecture-decision.md` is a good survey of the skills/MCP ecosystem.

**Take:** the rule JSON schema (generalised beyond code to *any* GRC artifact), `last_verified`/`law_version`
metadata on every knowledge item, and packaging our DPDPA knowledge as an Agent Skill for external AI tools.

---

## 11. dpdp-quick-audit: skip

`backend/main.py` concatenates a whole public repo and runs 10 `re.search` checks with no file/line
(e.g. "has `password=` and no `bcrypt`" → CRITICAL). Score = 100 − 25/15/8/2 per hit. Nothing here
beats compliance-copilot's scanner. **Skip.**

---

## 12. Cross-cutting lessons

1. **Nobody has memory.** Every repo is stateless per request or upload. None remembers accepted risks,
   rejected recommendations, prior findings or org context. **This is our moat.**
2. **Nobody observes change.** All are pull-based ("upload / ask"). None watches for a new vendor, a changed policy or expiring evidence.
3. **Verdicts come from prompts.** Four of the five LLM repos let the model decide PASS/FAIL. Only
   dpdp-law-to-code is deterministic. **Combine them: LLM → facts, rules → verdict.**
4. **Verifiers fail open.** GuardPulse and Rexskth both treat "couldn't verify" as "valid". **Ours must fail closed:
   an unverifiable citation downgrades the finding to "needs human review".**
5. **LLM-authored legal knowledge is wrong in subtle ways.** We found fabricated retention classes, a nonexistent
   "sensitive data" category and a §9 mix-up. **Corpus = official gazette text of the Act + Rules only,
   version-stamped, with `last_verified` dates.** Secondary sources may be used only as flagged commentary.
6. **Chunk by statute structure.** Section/sub-section/clause-aware chunking plus hybrid BM25+vector retrieval beats fixed-size chunks for law.
7. **Deployment:** Tushar (fully local), GovernAI (Ollama option) and TSI (self-hosted Docker) show that
   the **private deployment** option is expected in this market. Design the model layer to be provider-agnostic from day one.
8. **Licences are messy.** Five of the eleven have no licence or a restrictive one. Keep a `THIRD_PARTY.md` and only take code from MIT/Apache repos.

See [`../architecture/02-agent-blueprint.md`](../architecture/02-agent-blueprint.md) for how these lessons shape our design.
