# Third-Party Projects and Licence Register

Rule: **only MIT / Apache-2.0 (or similarly permissive) code may be copied or depended on.**
Projects with no licence file or restrictive terms are *reference only*. We may study their ideas
but must not copy code, data, or templates. Update this file whenever we take anything external.

| Project | Licence (verified from repo, Sep 2026) | How we may use it |
|---------|----------------------------------------|-------------------|
| [Wolfgangrush/dpdp-law-to-code](https://github.com/Wolfgangrush/dpdp-law-to-code) | MIT | Planned dependency (rule engine core). Keep copyright notice |
| [tsi-coop/tsi-dpdp-cms](https://github.com/tsi-coop/tsi-dpdp-cms) | Apache-2.0 | Integration target (connector); schema ideas. Keep NOTICE if code is copied |
| [ESR-style/compliance-copilot](https://github.com/ESR-style/compliance-copilot) | MIT | Rule-schema format, skill packaging |
| [ThomasMoreAI/legal-skills-open](https://github.com/ThomasMoreAI/legal-skills-open) | Apache-2.0 (repo); DPDPA skill files declare MIT from upstream authors | Prompt reference with attribution; verify every legal statement |
| [privacypriority/privacyanalyzer](https://github.com/privacypriority/privacyanalyzer) | Apache-2.0 | Engineering patterns only. **Do not use its legal content (contains errors)** |
| [yogeshwarbari/dpdp-quick-audit-vercel-deploy](https://github.com/yogeshwarbari/dpdp-quick-audit-vercel-deploy) | MIT | Not used |
| [AnakinSkywalker-0/guardpulse-connect](https://github.com/AnakinSkywalker-0/guardpulse-connect) | **No licence file** | Reference only |
| [Rexskth/Indian_compliance_multi_agent_RAG](https://github.com/Rexskth/Indian_compliance_multi_agent_RAG) | **No licence file** (README badge only) | Reference only |
| [sharadshriram/dpdpa-mcp](https://github.com/sharadshriram/dpdpa-mcp) | **No licence file** (README badge only) | Reference only. Re-implement Verhoeff from the published algorithm |
| [RushikeshSonwane03/GovernAI](https://github.com/RushikeshSonwane03/GovernAI) | **No licence file** | Reference only |
| [Tushar-9802/DPDPA](https://github.com/Tushar-9802/DPDPA) | **Source-available, commercial use prohibited** | Reference only. **Do not copy code, requirement data, or DOCX templates** |

Authoritative legal corpus: official Government of India publications of the Digital Personal Data
Protection Act, 2023 and the Digital Personal Data Protection Rules, 2025 (in `corpus/`; reproduction of
Acts and government notifications is permitted under Section 52(1)(q) of the Copyright Act, 1957).
The current files are copies pending a direct MeitY download; see `corpus/README.md`.

## Datasets

Full list with licence checks and quality notes: [`datasets/manifest.json`](datasets/manifest.json). Raw data is never committed.

| Dataset | Licence | Use |
|---------|---------|-----|
| [niketfuladi/dpdpa-2023-indian-privacy-policy-clause-level-risk](https://www.kaggle.com/datasets/niketfuladi/dpdpa-2023-indian-privacy-policy-clause-level-risk) | CC BY-SA 4.0 | Clause classification seed data. Attribute the author; ShareAlike applies if we redistribute derived data |
| [krishuppal/dark-patterns](https://www.kaggle.com/datasets/krishuppal/dark-patterns) | Apache-2.0 | Consent-validity signals |
| [alejopaullier/pii-external-dataset](https://www.kaggle.com/datasets/alejopaullier/pii-external-dataset) | Apache-2.0 | PII token classification |
| [verracodeguacas/ai4privacy-pii](https://www.kaggle.com/datasets/verracodeguacas/ai4privacy-pii) | **Ai4Privacy custom licence (Kaggle wrongly says MIT)** | **Not for commercial use without a corporate licence** |
| [jessemostipak/gdpr-violations](https://www.kaggle.com/datasets/jessemostipak/gdpr-violations) | Unknown | Internal evaluation only |
