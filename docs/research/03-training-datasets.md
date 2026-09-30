# Training and Evaluation Datasets (Kaggle and related)

**Date:** September 2026. **Status:** shortlist only, nothing downloaded yet.

> **Access note:** this cloud environment's network policy blocks `kaggle.com`, so this list comes from
> search-engine results. **Licences, sizes and contents are unverified.** Check each dataset page before downloading.

## 1. What "training" means for this agent

Per the [blueprint](../architecture/02-agent-blueprint.md), the LLM **does not decide compliance**. It extracts
facts, and the rule engine decides. So we are not fine-tuning a model to "know DPDPA". There are four things to train or tune:

| # | Component | Train or tune? | Data needed |
|---|-----------|----------------|-------------|
| A | **Fact extraction** from notices/policies (e.g. "does this notice state the purpose for each data item?") | Few-shot prompts first; fine-tune a small classifier later if needed | Labelled privacy-policy segments |
| B | **PII detection** (Aadhaar, PAN, names, addresses in documents and data stores) | Fine-tune a token classifier, plus a regex + checksum layer for IDs | PII NER datasets |
| C | **Retrieval** over the Act and Rules | Tune hybrid weights and the reranker | Question → correct provision pairs (**we must build this**) |
| D | **End-to-end agent quality** | No training; this is an **evaluation gate before deployment** | Fact patterns → expected verdicts (**we must build this**) |

**No public dataset labels anything against DPDPA.** Searches on Kaggle, Hugging Face and the wider web found only
GDPR-, US- and other-law datasets. Datasets C and D have to be authored by us from the gazette text and reviewed by counsel. That is also a moat.

## 2. Kaggle shortlist

### A. Privacy-policy understanding (fact extraction)
| Dataset | Why useful | Caveat |
|---------|-----------|--------|
| [GDPR Violations](https://www.kaggle.com/datasets/jessemostipak/gdpr-violations/data) | 2019 fines with violation text; realistic "what went wrong" cases to seed investigator test scenarios | GDPR, not DPDPA; map concepts manually |
| [Dark Patterns](https://www.kaggle.com/datasets/krishuppal/dark-patterns) | Text from e-commerce pages labelled for dark patterns → consent-validity signals (§6 "free", "unambiguous") | E-commerce UI text only |
| [GDPR-JSON](https://www.kaggle.com/datasets/gbasilveira/gdpr-json), [GDPR Articles](https://www.kaggle.com/datasets/josoriopt/gdpr-articles) | Structure template for how we store the DPDPA corpus; later a GDPR crosswalk | Reference only (the search listing says GDPR-JSON is CC BY 4.0) |
| [GDPR-qa-test-dataset](https://www.kaggle.com/datasets/iuliabunescu23/gdpr-qa-test-dataset) | Format template for our DPDPA retrieval eval set | GDPR |

**Better sources outside Kaggle** (the standard academic corpora, not on Kaggle):
[OPP-115](https://usableprivacy.org/data): 115 policies with 23k expert-annotated data practices.
[OPP-115 retention re-annotation](https://data.mendeley.com/datasets/c4x958pzpm/1) and
[OPP-115 purpose/consent re-annotation](https://data.mendeley.com/datasets/3dkh7f7tnh) are close analogues of DPDPA §5 notice checks.
PolicyQA and PrivacyQA are reading-comprehension sets over privacy policies. **OPP-115 is the best single source for task A.**
OPP-115's licence restricts commercial use (verify on the site), so use it for research and evaluation unless licensed.

### B. PII detection
| Dataset | Notes |
|---------|-------|
| [open-pii-masking-500k-ai4privacy](https://www.kaggle.com/datasets/mikedoes/open-pii-masking-500k-ai4privacy) | ~580k synthetic examples; the largest option. Check the ai4privacy licence (some versions are non-commercial) |
| [AI4privacy-PII](https://www.kaggle.com/datasets/verracodeguacas/ai4privacy-pii) | 54 PII classes |
| [Cleaned Repository of Annotated PII (CRAPII)](https://www.kaggle.com/datasets/langdonholmes/cleaned-repository-of-annotated-pii), [PIILO](https://www.kaggle.com/datasets/lburleigh/piilo-dataset) | ~22k real student essays with PII labels |
| [PII external dataset](https://www.kaggle.com/datasets/alejopaullier/pii-external-dataset) | 4.4k generated texts |

**Gap:** none of these has Indian identifiers. Aadhaar, PAN and mobile numbers are handled deterministically (regex + Verhoeff checksum).
For Indian names and addresses, consider the [sakshi-pii-in](https://huggingface.co/rotalabs/sakshi-pii-in) model (MuRIL fine-tune, Hinglish-aware)
and the [Indian Legal NER dataset](https://huggingface.co/datasets/AjayMukundS/Indian_Legal_NER_Dataset/tree/main).

### C. Breach and incident scenarios (Observer / Investigator test cases)
[Data Breaches Involving Personal Info 2020–2024](https://www.kaggle.com/datasets/umer7arooq/data-breaches-involving-personal-info-20202024),
[World's Biggest Data Breaches 2004–2025](https://www.kaggle.com/datasets/awallay/worlds-biggest-data-breaches-and-hacks-2004-2025),
[Data Breaches: a comprehensive list](https://www.kaggle.com/datasets/thedevastator/data-breaches-a-comprehensive-list).
Use these to generate realistic synthetic breach incidents for §8(6)/Rule 7 workflow tests.

### D. Indian legal text (low priority)
[Indian Supreme Court Judgments](https://www.kaggle.com/datasets/vangap/indian-supreme-court-judgments),
[SC Judgments 1950–2024](https://www.kaggle.com/datasets/adarshsingh0903/legal-dataset-sc-judgments-india-19502024),
[Indian Laws](https://www.kaggle.com/datasets/anishparkhe0401/indian-laws),
[LLM fine-tuning dataset of Indian legal texts](https://www.kaggle.com/datasets/akshatgupta7/llm-fine-tuning-dataset-of-indian-legal-texts) (IPC/CrPC/Constitution Q&A).
Useful later for DPB orders or case law. **Not a DPDPA knowledge source.** The corpus stays gazette-only.

**Skip:** generic "LLM Q&A" sets, synthetic "privacy" datasets with no legal labels, and GDPR fine trackers as a knowledge source.

## 3. What we must build ourselves (pre-deployment gate)

1. **DPDPA retrieval eval set** (~300 items): question → gold provision IDs (`DPDPA-2023:S8(6)`, `DPDPR-2025:R7`).
   Metrics: recall@5 and citation exact-match.
2. **Rule-engine fact-pattern suite** (~500 items): structured facts → expected verdict + citation. Extends `dpdp-law-to-code`'s
   407 tests to the Rules 2025 layer.
3. **Document → facts gold set** (~150 notices/policies): real Indian privacy notices (public web pages) labelled with DPDPA §5/Rule 3 facts.
   OPP-115 annotations can pre-label overlapping practices (purpose, retention, third-party sharing, user rights).
4. **Agent scenario suite** (~50 scenarios): synthetic org + event (new vendor, changed notice, breach) → expected findings,
   including "must NOT flag" cases and memory cases (accepted risk that must stay suppressed, or resurface when facts change).

**Deployment gate (proposal):** retrieval recall@5 ≥ 0.95; zero wrong citations on the rule suite; fact-extraction F1 ≥ 0.85
per field; **no scenario where an unverifiable claim is shown as verified**.

## 4. Next steps
1. Allow `kaggle.com` (and `www.kaggle.com`) in the cloud environment's network settings, or download locally.
2. Verify each shortlisted dataset's licence on its page. Add the approved ones to `THIRD_PARTY.md`.
3. Start dataset #1 (retrieval eval), since it depends only on the Act and Rules text.
