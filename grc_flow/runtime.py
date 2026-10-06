"""Wires settings to adapters once per process (API server or worker)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from grc_flow.adapters import observability as obs
from grc_flow.adapters.db import Store, new_id, now
from grc_flow.adapters.kv import KV, make_kv
from grc_flow.adapters.n8n import Outbound
from grc_flow.adapters.vectors import MemoryIndex, VectorIndex, make_index
from grc_flow.agents.checker import Checker
from grc_flow.agents.extractor import Extractor, make_extractor
from grc_flow.config import Settings
from grc_flow.pipeline import events
from grc_flow.rag.init import load_built_corpus
from grc_flow.rag.layer import NS_LAW, RagLayer


@dataclass
class Runtime:
    settings: Settings
    store: Store
    kv: KV
    index: VectorIndex
    rag: RagLayer | None
    extractor: Extractor
    outbound: Outbound
    checker: Checker

    @classmethod
    def build(cls, settings: Settings, **overrides: Any) -> Runtime:
        problems = settings.validate_for_production()
        if problems:
            raise SystemExit("Refusing to start in production: " + "; ".join(problems))
        obs.init(settings)
        index = overrides.get("index") or make_index(settings)
        if "rag" in overrides:
            rag = overrides["rag"]
        else:
            built = load_built_corpus(settings.corpus_dir)
            rag = RagLayer(index, *built) if built else None
            if rag and isinstance(index, MemoryIndex) and not index.count(NS_LAW):
                # The local index lives in this process only; fill it from the built corpus.
                rag.sync_knowledge()
        return cls(
            settings=settings,
            store=overrides.get("store") or Store(settings.database_target),
            kv=overrides.get("kv") or make_kv(settings),
            index=index,
            rag=rag,
            extractor=overrides.get("extractor") or make_extractor(settings),
            outbound=overrides.get("outbound")
            or Outbound(settings.n8n_outbound_url, settings.n8n_webhook_secret),
            checker=Checker(),
        )

    def make_event(
        self,
        event_type: str,
        org_id: str,
        source: str,
        payload: dict[str, Any],
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        events.validate(event_type, payload)
        if source not in events.SOURCES:
            raise events.EventError(f"Unknown source {source!r}.")
        event_id = new_id("evt")
        if idempotency_key is None:
            # Checker canaries always run; anything else is deduplicated on its content.
            idempotency_key = (
                event_id
                if source == "checker"
                else hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
            )
        return {
            "id": event_id,
            "type": event_type,
            "org_id": org_id,
            "source": source,
            "idempotency_key": f"{org_id}:{event_type}:{idempotency_key}",
            "payload": payload,
            "received_at": now(),
        }

    def submit(self, event: dict[str, Any]) -> tuple[str, bool]:
        """Store and queue an event. Returns (event_id, accepted); a duplicate is not queued."""
        if not self.store.add_event(event):
            return event["id"], False
        self.kv.enqueue({"event_id": event["id"]})
        return event["id"], True

    def outbound_safe(self, event_type: str, data: dict[str, Any]) -> None:
        """Notify n8n; a failure is reported but never breaks the pipeline."""
        try:
            self.outbound.emit(event_type, data)
        except Exception as exc:
            obs.capture(exc, where="n8n_outbound", type=event_type)
