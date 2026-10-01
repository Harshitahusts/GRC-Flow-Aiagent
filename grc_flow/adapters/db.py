"""Pipeline storage: Supabase (PostgreSQL) in production, SQLite locally.

Connections come from GRC-Ai's `grc_agent.web.db.connect`, which already speaks
both backends (it rewrites `?` placeholders and SQLite-only syntax for psycopg).
So Supabase is simply its PostgreSQL connection string, e.g. the pooler URL
`postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres`.

Tables are prefixed `flow_` so they can live in the same database as GRC-Ai's
own tables without clashing.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from grc_agent.web import db as grc_db
from grc_agent.web import pg

SCHEMA = """
CREATE TABLE IF NOT EXISTS flow_events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    org_id TEXT NOT NULL,
    source TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flow_runs (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES flow_events(id),
    pipeline TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    checker_json TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS flow_steps (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES flow_runs(id),
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    ms INTEGER NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flow_findings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES flow_runs(id),
    org_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    citations_json TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    confidence TEXT NOT NULL,
    fact_hash TEXT NOT NULL,
    memory_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flow_decisions (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    check_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    fact_hash TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flow_checks (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    ok INTEGER NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS flow_runs_event ON flow_runs (event_id);
CREATE INDEX IF NOT EXISTS flow_steps_run ON flow_steps (run_id, seq);
CREATE INDEX IF NOT EXISTS flow_findings_org ON flow_findings (org_id, subject, check_name);
CREATE INDEX IF NOT EXISTS flow_decisions_org ON flow_decisions (org_id, subject, check_name);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Store:
    def __init__(self, target: str | Path):
        self.target = target
        if pg.is_postgres(target):
            schema = pg.schema_of(str(target))
            if schema != "public":
                with pg.Connection(pg.without_schema(str(target))) as c:
                    c.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        else:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(pg.pg_schema(SCHEMA) if pg.is_postgres(target) else SCHEMA)

    @property
    def backend(self) -> str:
        return "postgres" if pg.is_postgres(self.target) else "sqlite"

    @contextmanager
    def conn(self) -> Iterator[Any]:
        c = grc_db.connect(self.target)
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def ping(self) -> None:
        with self.conn() as c:
            c.execute("SELECT 1").fetchone()

    # Events and runs ---------------------------------------------------------------

    def add_event(self, event: dict[str, Any]) -> bool:
        """Insert an event. False if its idempotency key was seen before."""
        with self.conn() as c:
            seen = c.execute(
                "SELECT id FROM flow_events WHERE idempotency_key = ?", (event["idempotency_key"],)
            ).fetchone()
            if seen:
                return False
            c.execute(
                "INSERT INTO flow_events (id, type, org_id, source, idempotency_key, payload_json,"
                " received_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event["id"],
                    event["type"],
                    event["org_id"],
                    event["source"],
                    event["idempotency_key"],
                    json.dumps(event["payload"]),
                    event["received_at"],
                ),
            )
        return True

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM flow_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        out = dict(zip(row.keys(), tuple(row), strict=True))
        out["payload"] = json.loads(out.pop("payload_json"))
        return out

    def start_run(self, event_id: str, pipeline: str) -> str:
        run_id = new_id("run")
        with self.conn() as c:
            c.execute(
                "INSERT INTO flow_runs (id, event_id, pipeline, status, started_at)"
                " VALUES (?, ?, ?, 'running', ?)",
                (run_id, event_id, pipeline, now()),
            )
        return run_id

    def add_step(
        self, run_id: str, seq: int, name: str, status: str, ms: int, detail: dict
    ) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO flow_steps (run_id, seq, name, status, ms, detail_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, seq, name, status, ms, json.dumps(detail, default=str)),
            )

    def finish_run(
        self, run_id: str, status: str, checker: dict | None = None, error: str | None = None
    ) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE flow_runs SET status = ?, finished_at = ?, checker_json = ?, error = ?"
                " WHERE id = ?",
                (status, now(), json.dumps(checker) if checker else None, error, run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.conn() as c:
            run = c.execute(
                "SELECT r.*, e.org_id, e.type AS event_type FROM flow_runs r"
                " JOIN flow_events e ON e.id = r.event_id WHERE r.id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                return None
            steps = c.execute(
                "SELECT seq, name, status, ms, detail_json FROM flow_steps WHERE run_id = ?"
                " ORDER BY seq",
                (run_id,),
            ).fetchall()
            findings = c.execute(
                "SELECT * FROM flow_findings WHERE run_id = ? ORDER BY check_name", (run_id,)
            ).fetchall()
        out = _row(run)
        out["checker"] = json.loads(out.pop("checker_json") or "null")
        out["steps"] = [
            {"seq": s[0], "name": s[1], "status": s[2], "ms": s[3], "detail": json.loads(s[4])}
            for s in steps
        ]
        out["findings"] = [_finding(f) for f in findings]
        return out

    def list_runs(self, org_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT r.id, r.pipeline, r.status, r.started_at, r.finished_at, e.type, e.org_id"
                " FROM flow_runs r JOIN flow_events e ON e.id = r.event_id"
                " WHERE e.org_id = ? ORDER BY r.started_at DESC LIMIT ?",
                (org_id, limit),
            ).fetchall()
        keys = ("id", "pipeline", "status", "started_at", "finished_at", "event_type", "org_id")
        return [dict(zip(keys, tuple(r), strict=True)) for r in rows]

    # Findings and memory -----------------------------------------------------------

    def add_finding(self, run_id: str, org_id: str, finding: dict[str, Any]) -> str:
        fid = new_id("fnd")
        with self.conn() as c:
            c.execute(
                "INSERT INTO flow_findings (id, run_id, org_id, subject, check_name, status,"
                " reason, citations_json, facts_json, confidence, fact_hash, memory_json,"
                " created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fid,
                    run_id,
                    org_id,
                    finding["subject"],
                    finding["check"],
                    finding["status"],
                    finding["reason"],
                    json.dumps(finding["citations"]),
                    json.dumps(finding["facts"]),
                    finding["confidence"],
                    finding["fact_hash"],
                    json.dumps(finding["memory"]) if finding.get("memory") else None,
                    now(),
                ),
            )
        return fid

    def add_decision(self, decision: dict[str, Any]) -> str:
        did = new_id("dec")
        with self.conn() as c:
            c.execute(
                "INSERT INTO flow_decisions (id, org_id, subject, check_name, decision, reason,"
                " decided_by, fact_hash, expires_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    did,
                    decision["org_id"],
                    decision["subject"],
                    decision["check"],
                    decision["decision"],
                    decision["reason"],
                    decision["decided_by"],
                    decision["fact_hash"],
                    decision.get("expires_at"),
                    now(),
                ),
            )
        return did

    def active_decision(
        self, org_id: str, subject: str, check: str, fact_hash: str
    ) -> dict[str, Any] | None:
        """A human decision still valid for these exact facts (same hash, not expired)."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM flow_decisions WHERE org_id = ? AND subject = ? AND check_name = ?"
                " ORDER BY created_at DESC",
                (org_id, subject, check),
            ).fetchall()
        for row in map(_row, rows):
            if row["fact_hash"] != fact_hash:
                continue
            if row["expires_at"] and row["expires_at"] < now():
                continue
            return row
        return None

    def last_decision(self, org_id: str, subject: str, check: str) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM flow_decisions WHERE org_id = ? AND subject = ? AND check_name = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (org_id, subject, check),
            ).fetchone()
        return _row(row) if row else None

    # Checker results ---------------------------------------------------------------

    def add_check(self, kind: str, target: str, ok: bool, detail: dict) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO flow_checks (kind, target, ok, detail_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (kind, target, int(ok), json.dumps(detail, default=str), now()),
            )

    def recent_checks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT kind, target, ok, detail_json, created_at FROM flow_checks"
                " ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"kind": r[0], "target": r[1], "ok": bool(r[2]), "detail": json.loads(r[3]), "at": r[4]}
            for r in rows
        ]


def _row(row: Any) -> dict[str, Any]:
    return dict(zip(row.keys(), tuple(row), strict=True))


def _finding(row: Any) -> dict[str, Any]:
    out = _row(row)
    out["citations"] = json.loads(out.pop("citations_json"))
    out["facts"] = json.loads(out.pop("facts_json"))
    out["memory"] = json.loads(out.pop("memory_json") or "null")
    return out
