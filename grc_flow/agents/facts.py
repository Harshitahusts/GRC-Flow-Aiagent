"""The facts the pipeline extracts from a privacy notice, and how they're judged.

Facts mirror the fields of `dpdp.types.NoticeRecord` from dpdp-law-to-code, so the
rule engine decides compliance and the LLM only reports what the document says.
A fact is "yes", "no" or "unknown". A "yes" must carry a verbatim quote from the
document; the checker rejects any "yes" whose quote isn't really there.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

VALUES = ("yes", "no", "unknown")
CONFIDENCE = ("high", "medium", "low")


@dataclass(frozen=True)
class FactSpec:
    name: str
    question: str
    from_document: bool = True  # False: a process fact the document can't prove


NOTICE_FACTS: tuple[FactSpec, ...] = (
    FactSpec(
        "describes_personal_data",
        "Does the notice describe the personal data (or categories of it) that will be processed?",
    ),
    FactSpec(
        "describes_purpose",
        "Does the notice state the purpose(s) for which the personal data will be processed?",
    ),
    FactSpec(
        "describes_rights_exercise_method",
        "Does the notice explain how the person can exercise their rights, such as "
        "withdrawing consent or using the grievance redressal mechanism?",
    ),
    FactSpec(
        "describes_complaint_method_to_board",
        "Does the notice explain how the person can complain to the Data Protection Board "
        "of India?",
    ),
    FactSpec(
        "available_in_english_or_eighth_schedule_language",
        "Is the notice in English or a language in the Eighth Schedule to the Constitution, "
        "or does it offer a version in one?",
    ),
    FactSpec(
        "is_given_before_or_with_consent_request",
        "Is the notice shown before, or together with, the request for consent?",
        from_document=False,
    ),
)
FACT_NAMES = tuple(f.name for f in NOTICE_FACTS)


@dataclass
class Fact:
    name: str
    value: str  # yes | no | unknown
    quote: str = ""
    confidence: str = "low"
    source: str = "llm"  # llm | offline | system | human
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def fact_hash(subject: str, check: str, facts: list[Fact]) -> str:
    """Identity of the facts a decision was made on. Quotes are excluded on purpose:
    rewording a notice without changing what it says shouldn't resurface a finding."""
    material = {
        "subject": subject,
        "check": check,
        "facts": sorted((f.name, f.value) for f in facts),
    }
    return hashlib.sha256(json.dumps(material).encode()).hexdigest()[:16]


def extraction_schema() -> dict:
    """JSON schema for Claude's structured output: one entry per document fact."""
    fact = {
        "type": "object",
        "properties": {
            "value": {"type": "string", "enum": list(VALUES)},
            "quote": {"type": "string"},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        },
        "required": ["value", "quote", "confidence"],
        "additionalProperties": False,
    }
    names = [f.name for f in NOTICE_FACTS if f.from_document]
    return {
        "type": "object",
        "properties": {
            "facts": {
                "type": "object",
                "properties": {n: fact for n in names},
                "required": names,
                "additionalProperties": False,
            }
        },
        "required": ["facts"],
        "additionalProperties": False,
    }
