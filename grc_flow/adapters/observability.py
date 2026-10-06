"""Sentry error tracking and performance spans, or plain logging when no DSN is set.

Personal data never goes to Sentry: `send_default_pii` is off, and `before_send`
drops request bodies, because events can carry privacy-notice text and payloads.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("grc_flow")
_enabled = False


def _scrub(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        headers = request.get("headers") or {}
        for h in ("authorization", "Authorization", "x-grc-signature", "X-GRC-Signature"):
            headers.pop(h, None)
    return event


def init(settings) -> bool:
    global _enabled
    if not settings.sentry_dsn:
        _enabled = False
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=f"grc-flow@{__import__('grc_flow').__version__}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        before_send=_scrub,
    )
    _enabled = True
    return True


def capture(exc: BaseException, **tags: Any) -> None:
    log.error("%s: %s %s", type(exc).__name__, exc, tags)
    if not _enabled:
        return
    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        for k, v in tags.items():
            scope.set_tag(k, str(v))
        sentry_sdk.capture_exception(exc)


def message(text: str, level: str = "warning", **tags: Any) -> None:
    getattr(log, level if level != "fatal" else "critical", log.warning)("%s %s", text, tags)
    if not _enabled:
        return
    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        for k, v in tags.items():
            scope.set_tag(k, str(v))
        sentry_sdk.capture_message(text, level=level)


@contextmanager
def span(op: str, name: str) -> Iterator[None]:
    if not _enabled:
        yield
        return
    import sentry_sdk

    with sentry_sdk.start_span(op=op, name=name):
        yield
