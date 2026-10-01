"""HTTP API for the website (Clerk-authenticated) and n8n (HMAC-signed).

Website (GRC-Ai)                          n8n
  POST /v1/events        submit an event    POST /v1/hooks/n8n   submit an event
  GET  /v1/runs          recent runs        (signed; see adapters/n8n.py)
  GET  /v1/runs/{id}     steps + findings
  POST /v1/decisions     accept / dismiss a finding (agent memory)
  POST /v1/rag/search    search the Act and Rules
Ops (Clerk org admins)
  GET  /health           liveness, no auth (for the load balancer / uptime checks)
  GET  /health/deep      every integration, via the checker agent
  POST /v1/checker/canary  run the canaries now
  GET  /v1/checker/checks  recent checker results
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from grc_flow import __version__
from grc_flow.adapters import n8n
from grc_flow.adapters.auth import Principal, require_admin, require_user
from grc_flow.config import Settings
from grc_flow.pipeline.events import EventError
from grc_flow.runtime import Runtime

RATE_LIMIT_PER_MIN = 60
User = Annotated[Principal, Depends(require_user)]


class EventIn(BaseModel):
    type: str
    payload: dict[str, Any]
    idempotency_key: str | None = Field(default=None, max_length=200)


class HookIn(EventIn):
    org_id: str = Field(min_length=1, max_length=100)


class DecisionIn(BaseModel):
    subject: str
    check: str
    decision: Literal["accepted_risk", "dismissed"]
    reason: str = Field(min_length=10, max_length=2000)
    fact_hash: str
    expires_at: str | None = None


class SearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    k: int = Field(default=5, ge=1, le=20)


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    rt = runtime or Runtime.build(settings)
    app = FastAPI(title="GRC-Flow", version=__version__)
    app.state.settings = rt.settings
    app.state.rt = rt

    def submit(event_type: str, org_id: str, source: str, body: EventIn) -> JSONResponse:
        if rt.kv.hit_rate_limit(f"events:{org_id}", RATE_LIMIT_PER_MIN, 60):
            raise HTTPException(429, "Too many events; slow down.")
        try:
            event = rt.make_event(event_type, org_id, source, body.payload, body.idempotency_key)
        except EventError as exc:
            raise HTTPException(422, str(exc)) from None
        event_id, accepted = rt.submit(event)
        return JSONResponse(
            {"event_id": event_id, "accepted": accepted, "duplicate": not accepted},
            status_code=202 if accepted else 200,
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/health/deep")
    def health_deep(user: User) -> dict[str, Any]:
        require_admin(user)
        return rt.checker.health(rt)

    @app.post("/v1/events")
    def post_event(body: EventIn, user: User) -> JSONResponse:
        return submit(body.type, user.org_id, "web", body)

    @app.post("/v1/hooks/n8n")
    async def n8n_hook(request: Request) -> JSONResponse:
        raw = await request.body()
        if not n8n.verify(
            rt.settings.n8n_webhook_secret,
            request.headers.get("x-grc-timestamp", ""),
            request.headers.get("x-grc-signature", ""),
            raw,
        ):
            raise HTTPException(401, "Bad or missing signature.")
        try:
            body = HookIn.model_validate(json.loads(raw))
        except ValueError as exc:
            raise HTTPException(422, f"Invalid body: {exc}") from None
        return submit(body.type, body.org_id, "n8n", body)

    @app.post("/v1/hooks/n8n/checker")
    async def n8n_checker(request: Request) -> dict[str, Any]:
        """Scheduled checker runs from n8n (cron): health, canary or sweep."""
        raw = await request.body()
        if not n8n.verify(
            rt.settings.n8n_webhook_secret,
            request.headers.get("x-grc-timestamp", ""),
            request.headers.get("x-grc-signature", ""),
            raw,
        ):
            raise HTTPException(401, "Bad or missing signature.")
        action = (json.loads(raw or b"{}") or {}).get("action")
        if action == "health":
            return rt.checker.health(rt)
        if action == "canary":
            return rt.checker.run_canaries(rt)
        if action == "sweep":
            return {"requeued": rt.checker.sweep_stale_runs(rt)}
        raise HTTPException(422, "action must be health, canary or sweep.")

    @app.get("/v1/runs")
    def runs(user: User, limit: int = 50) -> list[dict[str, Any]]:
        return rt.store.list_runs(user.org_id, min(max(limit, 1), 200))

    @app.get("/v1/runs/{run_id}")
    def run_detail(run_id: str, user: User) -> dict[str, Any]:
        run = rt.store.get_run(run_id)
        if run is None or run["org_id"] != user.org_id:
            raise HTTPException(404, "No such run.")
        return run

    @app.post("/v1/decisions", status_code=201)
    def decide(body: DecisionIn, user: User) -> dict[str, str]:
        decision_id = rt.store.add_decision(
            {**body.model_dump(), "org_id": user.org_id, "decided_by": user.user_id}
        )
        return {"decision_id": decision_id}

    @app.post("/v1/rag/search")
    def rag_search(body: SearchIn, user: User) -> dict[str, Any]:
        if rt.retriever is None:
            raise HTTPException(503, "The corpus isn't built yet (grc-flow rag-init).")
        hits = rt.retriever.search(body.query, body.k)
        return {
            "corpus_version": rt.corpus_version,
            "hits": [
                {
                    "ref": h.ref,
                    "heading": h.heading,
                    "score": h.score,
                    "via": h.via,
                    "text": h.text[:1200],
                }
                for h in hits
            ],
        }

    @app.post("/v1/checker/canary")
    def canary(user: User) -> dict[str, Any]:
        require_admin(user)
        return rt.checker.run_canaries(rt)

    @app.get("/v1/checker/checks")
    def checks(user: User, limit: int = 50) -> list[dict[str, Any]]:
        require_admin(user)
        return rt.store.recent_checks(min(max(limit, 1), 200))

    return app
