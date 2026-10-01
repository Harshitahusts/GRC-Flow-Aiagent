"""Baseline clause classifier on the DPDPA clause-risk dataset.

Trains TF-IDF + logistic regression for three targets (category, risk_label,
dpdpa_section) and evaluates with company-grouped cross-validation: every fold
holds out whole companies, so scores reflect performance on an unseen policy,
not memorised boilerplate from the same document.

This is a floor to beat, not the production approach (see
docs/research/03-training-datasets.md). Requires scikit-learn.

Usage: python scripts/fetch_datasets.py --only dpdpa_clauses
       python scripts/train_clause_baseline.py
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "raw" / "dpdpa_clauses" / "dpdpa_training_ready.csv"
OUT = ROOT / "reports" / "clause_baseline.json"
TARGETS = ("category", "risk_label", "dpdpa_section")
N_FOLDS = 5


def fix_mojibake(text: str) -> str:
    """Undo UTF-8 bytes that were decoded as Latin-1 (e.g. 'â\x80\x9c')."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def load() -> list[dict]:
    with DATA.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["text"] = fix_mojibake(r["text"])
    return rows


def evaluate(rows: list[dict], target: str) -> dict:
    texts = [r["text"] for r in rows]
    labels = [r[target] for r in rows]
    groups = [r["company"] for r in rows]

    preds, dummy_preds, gold = [], [], []
    for train_idx, test_idx in GroupKFold(n_splits=N_FOLDS).split(texts, labels, groups):
        x_tr = [texts[i] for i in train_idx]
        y_tr = [labels[i] for i in train_idx]
        x_te = [texts[i] for i in test_idx]

        model = make_pipeline(
            TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
            LogisticRegression(max_iter=2000, class_weight="balanced"),
        )
        model.fit(x_tr, y_tr)
        preds += list(model.predict(x_te))

        dummy = DummyClassifier(strategy="most_frequent").fit(x_tr, y_tr)
        dummy_preds += list(dummy.predict(x_te))
        gold += [labels[i] for i in test_idx]

    labels_seen = sorted(set(gold))
    per_class = f1_score(gold, preds, labels=labels_seen, average=None, zero_division=0)
    return {
        "n": len(gold),
        "classes": dict(Counter(labels)),
        "macro_f1": round(f1_score(gold, preds, average="macro", zero_division=0), 3),
        "accuracy": round(accuracy_score(gold, preds), 3),
        "majority_baseline_accuracy": round(accuracy_score(gold, dummy_preds), 3),
        "majority_baseline_macro_f1": round(
            f1_score(gold, dummy_preds, average="macro", zero_division=0), 3
        ),
        "per_class_f1": {c: round(s, 3) for c, s in zip(labels_seen, per_class, strict=True)},
    }


def main() -> None:
    rows = load()
    results = {
        "dataset": "niketfuladi/dpdpa-2023-indian-privacy-policy-clause-level-risk",
        "model": "tfidf(1-2gram)+logreg(balanced)",
        "cv": f"GroupKFold(n_splits={N_FOLDS}) by company",
        "targets": {t: evaluate(rows, t) for t in TARGETS},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2) + "\n")
    for t, r in results["targets"].items():
        print(
            f"{t:14} macro-F1 {r['macro_f1']:.3f}  acc {r['accuracy']:.3f}  "
            f"(majority: acc {r['majority_baseline_accuracy']:.3f}, "
            f"macro-F1 {r['majority_baseline_macro_f1']:.3f})"
        )
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
