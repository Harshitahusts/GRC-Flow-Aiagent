"""n8n integration, both directions, signed with a shared secret.

Inbound (n8n -> GRC-Flow): n8n's HTTP Request node posts JSON to /v1/hooks/n8n
with headers `X-GRC-Timestamp` (unix seconds) and
`X-GRC-Signature: sha256=<hex HMAC of "<timestamp>.<raw body>">`. Requests older
than 5 minutes are refused, so a captured request can't be replayed later.

Outbound (GRC-Flow -> n8n): run results and checker alerts are posted to one n8n
webhook URL with the same signature scheme; the n8n workflow verifies it and
routes by `type` (Slack, email, GRC-Ai notification, ticket).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

MAX_SKEW_S = 300


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(
    secret: str, timestamp: str, signature: str, body: bytes, now: float | None = None
) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now or time.time()) - ts) > MAX_SKEW_S:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


class Outbound:
    def __init__(self, url: str, secret: str, client: httpx.Client | None = None) -> None:
        self.url = url
        self.secret = secret
        self.client = client or httpx.Client(timeout=10)
        self.sent: list[dict[str, Any]] = []  # kept for tests and /health/deep

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.secret)

    def emit(self, event_type: str, data: dict[str, Any]) -> bool:
        payload = {"type": event_type, "sent_at": int(time.time()), "data": data}
        self.sent = (self.sent + [payload])[-20:]
        if not self.enabled:
            return False
        # Compact, unescaped JSON: n8n's JavaScript re-serialises the parsed body with
        # JSON.stringify to check the signature, and that produces exactly this form.
        body = json.dumps(payload, default=str, separators=(",", ":"), ensure_ascii=False).encode()
        ts = str(int(time.time()))
        response = self.client.post(
            self.url,
            content=body,
            headers={
                "content-type": "application/json",
                "x-grc-timestamp": ts,
                "x-grc-signature": sign(self.secret, ts, body),
            },
        )
        response.raise_for_status()
        return True
