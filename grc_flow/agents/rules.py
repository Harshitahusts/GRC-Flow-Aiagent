"""Deterministic compliance decisions over extracted facts (dpdp-law-to-code).

Each check needs specific facts. If any is unknown, or only low-confidence, the
check isn't run and the finding is `needs_human_review`: the rules never guess.
Citations come from the rule library and are rewritten to the corpus's citation
form ("DPDP Act 2023, Sec 5(1)(i)" -> "Section 5(1)(i)"); if the corpus doesn't
index that clause, the most specific parent that it does index is cited.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from dpdp.notice import (
    check_notice_board_complaint,
    check_notice_describes_data,
    check_notice_language,
    check_notice_rights_exercise,
    check_notice_timing,
)
from dpdp.types import ComplianceResult, NoticeRecord

from grc_flow.agents.facts import FACT_NAMES, Fact, fact_hash


@dataclass(frozen=True)
class Check:
    name: str
    title: str
    needs: tuple[str, ...]
    fn: Callable[[NoticeRecord], ComplianceResult]


NOTICE_CHECKS: tuple[Check, ...] = (
    Check(
        "notice.describes_data",
        "Notice describes the personal data and purpose",
        ("describes_personal_data", "describes_purpose"),
        check_notice_describes_data,
    ),
    Check(
        "notice.rights_exercise",
        "Notice explains how to exercise rights",
        ("describes_rights_exercise_method",),
        check_notice_rights_exercise,
    ),
    Check(
        "notice.board_complaint",
        "Notice explains how to complain to the Board",
        ("describes_complaint_method_to_board",),
        check_notice_board_complaint,
    ),
    Check(
        "notice.timing",
        "Notice is given before or with the consent request",
        ("is_given_before_or_with_consent_request",),
        check_notice_timing,
    ),
    Check(
        "notice.language",
        "Notice is in English or an Eighth Schedule language",
        ("available_in_english_or_eighth_schedule_language",),
        check_notice_language,
    ),
)

_LIB_CITE = re.compile(r"Sec\s+(\d+[A-Z]?(?:\([0-9a-z]+\))*)")


def to_corpus_citation(lib_citation: str, resolves: Callable[[str], bool]) -> str | None:
    """Most specific form of the library citation that exists in the corpus."""
    m = _LIB_CITE.search(lib_citation)
    if not m:
        return None
    ref = f"Section {m.group(1)}"
    while True:
        if resolves(ref):
            return ref
        if not ref.endswith(")"):
            return None
        ref = ref[: ref.rindex("(")]


def evaluate(subject: str, facts: list[Fact], resolves: Callable[[str], bool]) -> list[dict]:
    by_name = {f.name: f for f in facts}
    findings = []
    for check in NOTICE_CHECKS:
        used = [by_name[n] for n in check.needs if n in by_name]
        missing = [n for n in check.needs if n not in by_name]
        blocked = missing + [f.name for f in used if f.value == "unknown" or f.confidence == "low"]
        base = {
            "subject": subject,
            "check": check.name,
            "title": check.title,
            "facts": [f.to_dict() for f in used],
            "fact_hash": fact_hash(subject, check.name, used),
            "confidence": min(
                (f.confidence for f in used), key=("low", "medium", "high").index, default="low"
            ),
        }
        if blocked:
            findings.append(
                {
                    **base,
                    "status": "needs_human_review",
                    "reason": "Not enough to decide: "
                    + ", ".join(sorted(set(blocked)))
                    + " is unknown or low-confidence. Confirm it and re-run.",
                    "citations": [],
                    "rule": None,
                }
            )
            continue
        # Each check reads only its own fields; the others are placeholders.
        record = NoticeRecord(
            **{n: by_name[n].value == "yes" if n in check.needs else True for n in FACT_NAMES}
        )
        result = check.fn(record)
        cite = to_corpus_citation(result.citation, resolves)
        findings.append(
            {
                **base,
                "status": "compliant" if result.compliant else "gap",
                "reason": result.reason,
                "citations": [cite] if cite else [],
                "rule": {
                    "library": "dpdp-law-to-code",
                    "section": result.section,
                    "citation": result.citation,
                },
            }
        )
    return findings
