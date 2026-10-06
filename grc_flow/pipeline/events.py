"""Event types the backend accepts, and which pipeline handles each.

Sources: the GRC-Ai website (`web`), n8n automations (`n8n`), and the checker's
canaries (`checker`). Every event has an idempotency key, so n8n retries or a
double-clicked upload never create two runs.
"""

from __future__ import annotations

from typing import Any

EVENT_PIPELINES: dict[str, str] = {
    "document.uploaded": "notice_review",
    "document.changed": "notice_review",
    "vendor.added": "triage",
    "evidence.expiring": "triage",
    "dataflow.changed": "triage",
}
SOURCES = ("web", "n8n", "checker")


class EventError(ValueError):
    pass


def pipeline_for(event: dict[str, Any]) -> str:
    pipeline = EVENT_PIPELINES.get(event["type"])
    if pipeline is None:
        raise EventError(f"Unknown event type {event['type']!r}.")
    if pipeline == "notice_review" and event["payload"].get("doc_type") != "privacy_notice":
        return "triage"  # only privacy notices have automated checks so far
    return pipeline


def validate(event_type: str, payload: dict[str, Any]) -> None:
    if event_type not in EVENT_PIPELINES:
        raise EventError(f"Unknown event type {event_type!r}. Known: {sorted(EVENT_PIPELINES)}")
    if not isinstance(payload, dict):
        raise EventError("payload must be an object.")
    if (
        EVENT_PIPELINES[event_type] == "notice_review"
        and payload.get("doc_type") == "privacy_notice"
    ):
        if not isinstance(payload.get("text"), str) or not payload["text"].strip():
            raise EventError("A privacy_notice event needs the notice text in payload.text.")
        if not payload.get("subject"):
            raise EventError("payload.subject (e.g. the document id in GRC-Ai) is required.")
