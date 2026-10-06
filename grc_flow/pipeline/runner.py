"""The pipeline: a fixed sequence of steps, each one timed and recorded.

notice_review:
  load_document -> index_document (Pinecone, org namespace) -> retrieve_knowledge
  -> extract_facts -> ground_facts (checker)
  -> apply_rules -> apply_memory -> verify_findings (checker) -> persist -> notify

Run status:
  passed               every step ran and the checker found nothing blocking
                       (individual findings may still need human review);
  needs_human_review   the checker blocked something (bad citation, decision on an
                       unknown fact, a missing step);
  failed               a step raised. The error goes to Sentry and n8n.

Step details hold counts, refs and hashes, never the document text, so the run
log is safe to show and to keep.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any

from grc_flow.adapters import observability as obs
from grc_flow.agents import rules
from grc_flow.agents.checker import Issue
from grc_flow.agents.facts import NOTICE_FACTS, VALUES, Fact
from grc_flow.pipeline.events import pipeline_for

LAW_QUERY = (
    "notice to Data Principal describing personal data and purpose of processing, "
    "manner of exercising rights, complaint to the Board, language of notice"
)


class _Run:
    def __init__(self, rt, run_id: str) -> None:
        self.rt = rt
        self.run_id = run_id
        self.seq = 0
        self.steps: list[dict[str, Any]] = []

    def step(self, name: str, fn: Callable[[], dict[str, Any] | None]) -> None:
        self.seq += 1
        started = time.perf_counter()
        status, detail = "ok", {}
        try:
            with obs.span("pipeline.step", name):
                detail = fn() or {}
        except Exception as exc:
            status, detail = "failed", {"error": f"{type(exc).__name__}: {exc}"}
            raise
        finally:
            ms = int((time.perf_counter() - started) * 1000)
            self.rt.store.add_step(self.run_id, self.seq, name, status, ms, detail)
            self.steps.append({"name": name, "status": status})


def run_event(rt, event: dict[str, Any], notify: bool = True) -> dict[str, Any]:
    pipeline = pipeline_for(event)
    run_id = rt.store.start_run(event["id"], pipeline)
    run = _Run(rt, run_id)
    try:
        if pipeline == "notice_review":
            status, verdict = _notice_review(rt, run, event)
        else:
            status, verdict = _triage(run, event)
        rt.store.finish_run(run_id, status, checker=verdict)
    except Exception as exc:
        obs.capture(exc, run_id=run_id, pipeline=pipeline, event_type=event["type"])
        rt.store.finish_run(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
        if notify:
            rt.outbound_safe(
                "run.failed",
                {"run_id": run_id, "org_id": event["org_id"], "error": type(exc).__name__},
            )
        return rt.store.get_run(run_id)

    result = rt.store.get_run(run_id)
    if notify:
        rt.outbound_safe(
            "run.completed",
            {
                "run_id": run_id,
                "org_id": event["org_id"],
                "event_type": event["type"],
                "status": status,
                "findings": [
                    {"check": f["check_name"], "status": f["status"], "citations": f["citations"]}
                    for f in result["findings"]
                ],
            },
        )
    return result


def _triage(run: _Run, event: dict[str, Any]) -> tuple[str, dict]:
    """Events without automated checks yet: recorded and forwarded to n8n for routing."""
    run.step("record", lambda: {"type": event["type"], "keys": sorted(event["payload"])})
    return "passed", {"ok": True, "issues": [], "note": "No automated checks for this event."}


def _notice_review(rt, run: _Run, event: dict[str, Any]) -> tuple[str, dict]:
    payload = event["payload"]
    subject = payload["subject"]
    ctx: dict[str, Any] = {}
    issues: list[Issue] = []

    def load_document():
        if rt.rag is None:
            raise RuntimeError("The corpus isn't built. Run `grc-flow rag-init` first.")
        ctx["doc"] = payload["text"]
        return {
            "chars": len(ctx["doc"]),
            "sha256": hashlib.sha256(ctx["doc"].encode()).hexdigest()[:16],
        }

    def index_document():
        return rt.rag.index_document(
            event["org_id"], subject, ctx["doc"], title=payload.get("title", "")
        )

    def retrieve_knowledge():
        # Law goes to the extractor as context; guidance (register rows, notes) is
        # recorded so a reviewer can see what the analyst would draw on.
        hits = rt.rag.search(LAW_QUERY, k=5, scope=("law",))
        guidance = rt.rag.search(LAW_QUERY, k=3, scope=("guidance",))
        ctx["provisions"] = [{"ref": h.ref, "heading": h.heading, "text": h.text} for h in hits]
        ctx["guidance"] = [h.id for h in guidance]
        return {
            "refs": [h.ref for h in hits],
            "via": {h.ref: h.via for h in hits},
            "guidance": ctx["guidance"],
            "knowledge_version": rt.rag.version,
        }

    def extract_facts():
        facts = rt.extractor.extract(ctx["doc"], ctx["provisions"])
        # Process facts supplied by the website (e.g. where the notice is shown).
        for name, value in (payload.get("facts") or {}).items():
            if name in {f.name for f in NOTICE_FACTS} and value in VALUES:
                facts = [f for f in facts if f.name != name]
                facts.append(
                    Fact(
                        name, value, "", "high", "system", "Supplied by the website with the event."
                    )
                )
        ctx["facts"] = facts
        return {"extractor": rt.extractor.name, "facts": {f.name: f.value for f in facts}}

    def ground_facts():
        ctx["facts"], found = rt.checker.ground_facts(ctx["doc"], ctx["facts"])
        issues.extend(found)
        return {"downgraded": [i.detail.split(":")[0] for i in found]}

    def apply_rules():
        ctx["findings"] = rules.evaluate(subject, ctx["facts"], rt.rag.resolves)
        return {"statuses": {f["check"]: f["status"] for f in ctx["findings"]}}

    def apply_memory():
        notes = {}
        for fd in ctx["findings"]:
            if fd["status"] == "compliant":
                continue
            decision = rt.store.active_decision(
                event["org_id"], subject, fd["check"], fd["fact_hash"]
            )
            if decision:
                fd["memory"] = {
                    "suppressed_by": decision["id"],
                    "decision": decision["decision"],
                    "reason": decision["reason"],
                }
                notes[fd["check"]] = "suppressed"
                continue
            last = rt.store.last_decision(event["org_id"], subject, fd["check"])
            if last:
                fd["memory"] = {
                    "resurfaced": True,
                    "previous_decision": last["id"],
                    "note": "Facts changed since the last decision; review again.",
                }
                notes[fd["check"]] = "resurfaced"
        return {"memory": notes}

    def verify_findings():
        found = rt.checker.verify_findings(ctx["findings"], rt.rag.resolves)
        issues.extend(found)
        return {"issues": [i.code for i in found]}

    def persist():
        ids = [rt.store.add_finding(run.run_id, event["org_id"], fd) for fd in ctx["findings"]]
        return {"findings": len(ids)}

    for name, fn in (
        ("load_document", load_document),
        ("index_document", index_document),
        ("retrieve_knowledge", retrieve_knowledge),
        ("extract_facts", extract_facts),
        ("ground_facts", ground_facts),
        ("apply_rules", apply_rules),
        ("apply_memory", apply_memory),
        ("verify_findings", verify_findings),
        ("persist", persist),
    ):
        run.step(name, fn)

    issues.extend(rt.checker.verify_run(run.steps))
    blocking = [i for i in issues if i.severity == "block"]
    verdict = {"ok": not blocking, "issues": [i.to_dict() for i in issues]}
    rt.store.add_check("run", run.run_id, not blocking, verdict)
    return ("passed" if not blocking else "needs_human_review"), verdict
