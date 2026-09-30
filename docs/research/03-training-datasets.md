# Training and Evaluation Datasets (Kaggle and related)

**Date:** September 2026. **Status:** Kaggle searched via its API (28 queries, 804 unique datasets), 10 downloaded and
inspected, first baseline trained. The machine-readable list is [`datasets/manifest.json`](../../datasets/manifest.json).
Fetch with `python scripts/fetch_datasets.py`. Raw data is git-ignored.

## 0. Verified findings (after download)

1. **One DPDPA-labelled dataset exists:** [`niketfuladi/dpdpa-2023-indian-privacy-policy-clause-level-risk`](https://www.kaggle.com/datasets/niketfuladi/dpdpa-2023-indian-privacy-policy-clause-level-risk)
   (CC BY-SA 4.0). It has 816 clauses from Zomato, Nykaa, Swiggy, BigBasket, PhonePe, Flipkart, Razorpay and UIDAI, plus 55 synthetic ones.
   Labels: `category` (9), `risk_label` (green/gray/red), `dpdpa_section`, `notes`. This corrects the earlier "no DPDPA dataset" statement below.
   **It is noisy:** 59% of clauses are tagged S.5, the notes read as LLM-written, some sections are wrong (consent withdrawal tagged S.11 instead of S.6(4)),
   21 rows have mojibake, and `risk_label` measures user risk, not a compliance verdict. **Use it as seed and pre-label data; re-annotate for gold.**
2. **ai4privacy is mislabelled on Kaggle.** It is listed as MIT, but the licence file inside requires a paid corporate licence for organisations
   with more than about 3 staff. **Do not use it commercially without that licence.**
3. **The "valid Aadhaar" synthetic set is not valid.** In `sachintiwaryy/indian-fintech-synthetic-dataset-free-sample`, only 1,011 of 10,000
   Aadhaar numbers pass Verhoeff, which is chance level, and the names and cities are American. Use it only as negative test cases for our detector.
4. **Skip:** `deborareis/privacy-policies` (2018 app-policy *URLs*, not text), `yogeshm01/indian-legal-qa-dataset-10k-questions`
   (templated "who is the respondent" questions).

### First baseline: clause classifier (`scripts/train_clause_baseline.py`)
TF-IDF + logistic regression. 5-fold cross-validation **grouped by company** (each fold's companies are never seen in training).
Full numbers: [`reports/clause_baseline.json`](../../reports/clause_baseline.json).

| Target | Macro-F1 | Accuracy | Majority-class accuracy |
|--------|---------:|---------:|------------------------:|
| `category` (9 classes) | **0.596** | 0.694 | 0.407 |
| `risk_label` (3 classes) | **0.563** | 0.599 | 0.526 |
| `dpdpa_section` (11 classes) | **0.452** | 0.707 | 0.588 |

**What it tells us:** word features can recognise *topic* (category) reasonably well, but they barely beat always guessing the most
common label on *risk*. Judging risk needs reasoning about what a clause permits, which a keyword model cannot do. This supports the
blueprint: use an LLM to extract facts, measure it against a **re-annotated gold set**, and let rules decide. These numbers are the floor any LLM extractor must clearly beat.

> The sections below are the original search-based shortlist. Where they conflict with §0, §0 wins.

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
1. ~~Allow Kaggle access~~ (done). ~~Verify licences~~ (done for downloaded sets, see the manifest).
2. Re-annotate the 816 DPDPA clauses into a gold set with our fact schema: DPDPA/Rule-level facts, not green/gray/red. Start with the 55 synthetic and 12 UIDAI clauses as a pilot.
3. Run an LLM few-shot extractor on the same company-grouped folds and compare it with the baseline above.
4. Start dataset #1 (retrieval eval), since it depends only on the Act and Rules text.
