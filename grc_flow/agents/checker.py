"""The checker agent: makes sure every flow is working and every output is grounded.

It works at three levels, and it fails closed: anything it can't verify is marked
`needs_human_review`, never passed.

1. Inside every run (`ground_facts`, `verify_findings`, `verify_run`):
   - each "yes" fact's quote really appears in the document (exact match after
     whitespace and quote-mark normalisation); if not, the fact becomes "unknown";
   - each citation resolves in the ingested corpus;
   - a finding's status agrees with its facts (no "compliant" on unknown facts);
   - every pipeline step ran and reported ok.
2. Health probes (`health`): database, queue/cache, vector index (record count and
   corpus version vs the loaded corpus), corpus, LLM config, n8n, Sentry, auth.
3. Canaries (`run_canaries`): known notices with known answers go through the real
   pipeline; any difference from the expected statuses is an alert. Plus
   `sweep_stale_runs`, which re-queues runs a crashed worker left "running".

Results go to the `flow_checks` table, Sentry, and n8n (type `checker.alert`).
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from grc_flow.adapters import observability as obs
from grc_flow.agents.facts import Fact

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class Issue:
    severity: str  # block | warn
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "code": self.code, "detail": self.detail}


@dataclass
class Verdict:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "block" for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "issues": [i.to_dict() for i in self.issues]}


EXPECTED_STEPS = (
    "load_document",
    "retrieve_law",
    "extract_facts",
    "ground_facts",
    "apply_rules",
    "apply_memory",
    "verify_findings",
    "persist",
)


class Checker:
    # Per-run checks ----------------------------------------------------------------

    def ground_facts(self, document: str, facts: list[Fact]) -> tuple[list[Fact], list[Issue]]:
        """Downgrade any "yes" whose quote isn't verbatim in the document."""
        doc = normalise(document)
        issues = []
        for f in facts:
            if f.value != "yes" or f.source in ("system", "human"):
                continue
            if not f.quote or normalise(f.quote) not in doc:
                issues.append(
                    Issue(
                        "warn",
                        "quote_not_found",
                        f"{f.name}: quote not found in the document; set to unknown.",
                    )
                )
                f.value, f.confidence = "unknown", "low"
                f.note = "Checker: the quoted text was not found in the document."
        return facts, issues

    def verify_findings(self, findings: list[dict], resolves) -> list[Issue]:
        issues = []
        for fd in findings:
            facts = fd["facts"]
            if fd["status"] in ("compliant", "gap"):
                if any(f["value"] == "unknown" for f in facts):
                    issues.append(
                        Issue(
                            "block",
                            "decided_on_unknown",
                            f"{fd['check']}: decided although a fact is unknown.",
                        )
                    )
                    fd["status"] = "needs_human_review"
                if not fd["citations"]:
                    issues.append(
                        Issue(
                            "block",
                            "no_citation",
                            f"{fd['check']}: no citation resolves in the corpus.",
                        )
                    )
                    fd["status"] = "needs_human_review"
            for ref in fd["citations"]:
                if not resolves(ref):
                    issues.append(
                        Issue(
                            "block",
                            "citation_unresolved",
                            f"{fd['check']}: {ref} is not in the corpus.",
                        )
                    )
                    fd["status"] = "needs_human_review"
        return issues

    def verify_run(self, steps: list[dict]) -> list[Issue]:
        seen = {s["name"]: s["status"] for s in steps}
        issues = [
            Issue("block", "step_missing", f"Step {name} did not run.")
            for name in EXPECTED_STEPS
            if name not in seen
        ]
        issues += [
            Issue("block", "step_failed", f"Step {n} reported {s}.")
            for n, s in seen.items()
            if s != "ok"
        ]
        return issues

    # Health probes -----------------------------------------------------------------

    def health(self, rt) -> dict[str, Any]:
        s = rt.settings
        results: dict[str, dict[str, Any]] = {}

        def probe(name: str, fn) -> None:
            try:
                detail = fn() or {}
                results[name] = {"ok": detail.pop("ok", True), **detail}
            except Exception as exc:  # a probe failing is a finding, not a crash
                results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        probe("database", lambda: (rt.store.ping(), {"backend": rt.store.backend})[1])
        probe(
            "queue_cache",
            lambda: {"ok": rt.kv.ping(), "backend": rt.kv.name, "queue_depth": rt.kv.queue_depth()},
        )
        probe(
            "corpus",
            lambda: {
                "ok": rt.retriever is not None,
                "version": rt.corpus_version,
                "chunks": len(rt.retriever.corpus.chunks) if rt.retriever else 0,
                **({} if rt.retriever else {"error": "Run `grc-flow rag-init`."}),
            },
        )

        def vectors():
            count = rt.index.count()
            chunks = len(rt.retriever.corpus.chunks) if rt.retriever else None
            ok = count is not None and chunks is not None and count >= chunks
            out = {"ok": ok, "backend": rt.index.name, "records": count, "chunks": chunks}
            if rt.retriever:
                hits = rt.index.search("personal data breach intimation", 1)
                version = hits[0].fields.get("corpus_version") if hits else None
                out["version_matches"] = version == rt.corpus_version
                out["ok"] = ok and out["version_matches"]
            return out

        probe("vectors", vectors)
        probe(
            "llm",
            lambda: {
                "ok": s.ai_mode == "offline"
                or bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")),
                "mode": rt.extractor.name,
                "model": s.model if s.ai_mode == "api" else None,
            },
        )
        probe(
            "automation",
            lambda: {"ok": rt.outbound.enabled or not s.production, "n8n": rt.outbound.enabled},
        )
        probe(
            "errors",
            lambda: {"ok": bool(s.sentry_dsn) or not s.production, "sentry": bool(s.sentry_dsn)},
        )
        probe(
            "auth",
            lambda: {
                "ok": (s.auth_mode == "clerk" and bool(s.clerk_issuer))
                or (s.auth_mode == "dev" and not s.production),
                "mode": s.auth_mode,
            },
        )
        ok = all(r["ok"] for r in results.values())
        rt.store.add_check("health", "all", ok, results)
        if not ok:
            failing = [k for k, v in results.items() if not v["ok"]]
            obs.message(f"Health check failing: {', '.join(failing)}", "error", checker="health")
            rt.outbound_safe(
                "checker.alert", {"kind": "health", "failing": failing, "results": results}
            )
        return {"ok": ok, "services": results, "config": s.services()}

    # Canaries and recovery ---------------------------------------------------------

    def run_canaries(self, rt) -> dict[str, Any]:
        from grc_flow.canaries import CANARIES
        from grc_flow.pipeline.runner import run_event

        report = []
        for canary in CANARIES:
            event = rt.make_event(
                "document.uploaded",
                "org_canary",
                "checker",
                canary["payload"],
                idempotency_key=None,
            )
            rt.store.add_event(event)
            run = run_event(rt, event, notify=False)
            got = {f["check_name"]: f["status"] for f in run["findings"]}
            diffs = {
                k: {"expected": v, "got": got.get(k)}
                for k, v in canary["expected"].items()
                if got.get(k) != v
            }
            ok = not diffs and run["status"] in canary["run_status"]
            report.append(
                {
                    "canary": canary["name"],
                    "ok": ok,
                    "run_id": run["id"],
                    "run_status": run["status"],
                    "diffs": diffs,
                }
            )
        ok = all(r["ok"] for r in report)
        rt.store.add_check("canary", "notice_review", ok, {"results": report})
        if not ok:
            obs.message("Canary run differs from expected results", "error", checker="canary")
            rt.outbound_safe("checker.alert", {"kind": "canary", "results": report})
        return {"ok": ok, "results": report}

    def sweep_stale_runs(self, rt, older_than_min: int = 15) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=older_than_min)).isoformat(
            timespec="seconds"
        )
        with rt.store.conn() as c:
            stale = c.execute(
                "SELECT id, event_id FROM flow_runs WHERE status = 'running' AND started_at < ?",
                (cutoff,),
            ).fetchall()
            for run_id, _event_id in stale:
                c.execute("UPDATE flow_runs SET status = 'abandoned' WHERE id = ?", (run_id,))
        for _run_id, event_id in stale:
            rt.kv.enqueue({"event_id": event_id})
        if stale:
            rt.store.add_check("sweep", "runs", False, {"requeued": [r[1] for r in stale]})
            obs.message(f"Re-queued {len(stale)} abandoned run(s)", "warning", checker="sweep")
        return len(stale)
