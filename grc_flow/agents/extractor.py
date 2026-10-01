"""Fact extraction: Claude reads the notice and reports facts with verbatim quotes.

The call follows the same pattern GRC-Ai's assessor uses (`grc_agent.ai_assessment`):
structured JSON output against a strict schema, adaptive thinking, and the
server-side refusal fallback. Claude is never asked whether the notice complies,
only what it says; the rule engine decides compliance afterwards.

`OfflineExtractor` is a deterministic keyword stand-in for tests, canaries and
local development without an API key. It is not used in production.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from grc_flow.agents.facts import NOTICE_FACTS, Fact, extraction_schema

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_DOC_CHARS = 400_000  # ~100k tokens; refuse rather than silently truncate

SYSTEM_PROMPT = """\
You read privacy notices for a DPDPA (India's Digital Personal Data Protection Act 2023
and Rules 2025) compliance review. You report what the notice says; you do not judge
whether it complies. A rule engine and then a person do that.

For each fact:
- value "yes" only when the notice clearly says it. Then copy the shortest sentence or
  phrase that shows it into "quote", character for character from the NOTICE. Never
  paraphrase, fix typos or join separate sentences: the quote is checked by exact match.
- value "no" when the notice is silent or says the opposite. Leave "quote" empty.
- value "unknown" when the notice is ambiguous. Leave "quote" empty.
- confidence: "high" when the wording is explicit, "medium" when it needs light
  interpretation, "low" otherwise.

The provisions are included so you understand what each fact refers to. Do not cite them
and do not quote from them."""


class ExtractionError(RuntimeError):
    pass


class Extractor(Protocol):
    name: str

    def extract(self, document: str, provisions: list[dict[str, str]]) -> list[Fact]: ...


def _questions() -> str:
    return "\n".join(f"- {f.name}: {f.question}" for f in NOTICE_FACTS if f.from_document)


class ClaudeExtractor:
    name = "claude"

    def __init__(self, model: str, effort: str, client: Any | None = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort

    def _call(self, prompt: str) -> Any:
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": extraction_schema()},
            },
            fallbacks="default",
            betas=[FALLBACK_BETA],
        )

    def extract(self, document: str, provisions: list[dict[str, str]]) -> list[Fact]:
        if len(document) > MAX_DOC_CHARS:
            raise ExtractionError(
                f"The notice is {len(document):,} characters; the limit is {MAX_DOC_CHARS:,}. "
                "Split it and submit the privacy-notice section only."
            )
        law = "\n\n".join(f"[{p['ref']}] {p['heading']}\n{p['text']}" for p in provisions)
        prompt = (
            f"FACTS TO REPORT\n{_questions()}\n\n"
            f"PROVISIONS (context only)\n{law or '(none retrieved)'}\n\n"
            f"NOTICE\n<<<\n{document}\n>>>"
        )
        last = ""
        for _ in range(2):  # one retry on malformed output
            response = self._call(prompt)
            if response.stop_reason == "refusal":
                raise ExtractionError("Claude declined to read this document.")
            if response.stop_reason == "max_tokens":
                last = "output was cut off"
                continue
            text = next((b.text for b in response.content if b.type == "text"), "")
            try:
                data = json.loads(text)["facts"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                last = str(exc)
                continue
            return [
                Fact(
                    name=f.name,
                    value=data[f.name]["value"],
                    quote=data[f.name]["quote"].strip(),
                    confidence=data[f.name]["confidence"],
                    source="llm",
                )
                for f in NOTICE_FACTS
                if f.from_document
            ]
        raise ExtractionError(f"Unusable output after a retry ({last}).")


# Offline stand-in --------------------------------------------------------------------

_PATTERNS: dict[str, tuple[str, ...]] = {
    "describes_personal_data": (
        r"\bwe collect\b",
        r"\bpersonal (data|information) (we|that we) (collect|process)",
        r"\bcategories of (personal )?data\b",
    ),
    "describes_purpose": (r"\bpurpose", r"\bwe use (your|this|the) (data|information)\b"),
    "describes_rights_exercise_method": (
        r"\bwithdraw (your )?consent\b",
        r"\bgrievance\b",
        r"\bexercise (your|these) rights\b",
    ),
    "describes_complaint_method_to_board": (r"\bData Protection Board\b",),
}
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def _english(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) > 0.9


class OfflineExtractor:
    name = "offline"

    def extract(self, document: str, provisions: list[dict[str, str]]) -> list[Fact]:
        sentences = [s.strip() for s in _SENTENCE.findall(document) if s.strip()]
        facts = []
        for name, patterns in _PATTERNS.items():
            hit = next(
                (s for s in sentences for p in patterns if re.search(p, s, re.IGNORECASE)), None
            )
            facts.append(
                Fact(name, "yes", hit, "medium", "offline")
                if hit
                else Fact(name, "no", "", "medium", "offline")
            )
        if _english(document):
            first = sentences[0] if sentences else ""
            facts.append(
                Fact(
                    "available_in_english_or_eighth_schedule_language",
                    "yes",
                    first,
                    "high",
                    "offline",
                    "The notice is written in English.",
                )
            )
        else:
            facts.append(
                Fact(
                    "available_in_english_or_eighth_schedule_language",
                    "unknown",
                    "",
                    "low",
                    "offline",
                )
            )
        return facts


def make_extractor(settings, client: Any | None = None) -> Extractor:
    if settings.ai_mode == "offline":
        return OfflineExtractor()
    return ClaudeExtractor(settings.model, settings.effort, client)
