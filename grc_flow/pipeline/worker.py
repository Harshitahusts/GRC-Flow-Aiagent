"""Queue worker: pops events from Upstash and runs them.

Each event is claimed with a short-lived lock first, so two workers that somehow
get the same event (a re-queue racing a slow worker) never run it twice at once.
A job that keeps failing to even start goes to the dead-letter list.
"""

from __future__ import annotations

import time

from grc_flow.adapters import observability as obs
from grc_flow.pipeline.runner import run_event

LOCK_TTL_S = 900


def process_one(rt) -> dict | None:
    job = rt.kv.dequeue()
    if job is None:
        return None
    event_id = job.get("event_id", "")
    if not rt.kv.claim(f"event:{event_id}", LOCK_TTL_S):
        return {"event_id": event_id, "skipped": "already being processed"}
    event = rt.store.get_event(event_id)
    if event is None:
        rt.kv.dead_letter(job, "event not found in database")
        return {"event_id": event_id, "skipped": "unknown event"}
    try:
        run = run_event(rt, event)
    except Exception as exc:  # run_event records its own failures; this is a last resort
        obs.capture(exc, event_id=event_id, where="worker")
        rt.kv.dead_letter(job, f"{type(exc).__name__}: {exc}")
        return {"event_id": event_id, "error": str(exc)}
    return {"event_id": event_id, "run_id": run["id"], "status": run["status"]}


def run_forever(rt, idle_sleep_s: float = 2.0, sweep_every_s: float = 300.0) -> None:
    last_sweep = 0.0
    while True:
        if time.monotonic() - last_sweep > sweep_every_s:
            rt.checker.sweep_stale_runs(rt)
            last_sweep = time.monotonic()
        if process_one(rt) is None:
            time.sleep(idle_sleep_s)
