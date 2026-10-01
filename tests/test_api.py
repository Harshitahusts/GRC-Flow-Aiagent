from __future__ import annotations

import json
import time
from dataclasses import replace
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from grc_flow.adapters import n8n
from grc_flow.adapters.auth import verify_clerk_token
from grc_flow.api import create_app
from grc_flow.canaries import COMPLETE_NOTICE
from grc_flow.config import Settings
from grc_flow.pipeline.worker import process_one

ADMIN = {"x-dev-user": "user_1", "x-dev-org": "org_a"}


@pytest.fixture
def client(rt):
    return TestClient(create_app(rt.settings, rt))


def post_notice(client, headers=ADMIN, subject="doc:1"):
    return client.post(
        "/v1/events",
        headers=headers,
        json={
            "type": "document.uploaded",
            "payload": {"doc_type": "privacy_notice", "subject": subject, "text": COMPLETE_NOTICE},
        },
    )


def test_health_is_public(client):
    assert client.get("/health").json()["status"] == "ok"


def test_submit_run_and_read_back(client, rt):
    first = post_notice(client)
    assert first.status_code == 202 and first.json()["accepted"]
    assert post_notice(client).json()["duplicate"]
    run_id = process_one(rt)["run_id"]
    run = client.get(f"/v1/runs/{run_id}", headers=ADMIN).json()
    assert run["status"] == "passed" and len(run["findings"]) == 5
    assert client.get("/v1/runs", headers=ADMIN).json()[0]["id"] == run_id


def test_other_organisations_cannot_see_a_run(client, rt):
    post_notice(client)
    run_id = process_one(rt)["run_id"]
    other = {"x-dev-user": "u2", "x-dev-org": "org_b"}
    assert client.get(f"/v1/runs/{run_id}", headers=other).status_code == 404
    assert client.get("/v1/runs", headers=other).json() == []


def test_bad_events_are_rejected(client):
    r = client.post(
        "/v1/events",
        headers=ADMIN,
        json={"type": "document.uploaded", "payload": {"doc_type": "privacy_notice"}},
    )
    assert r.status_code == 422
    assert (
        client.post("/v1/events", headers=ADMIN, json={"type": "nope", "payload": {}}).status_code
        == 422
    )


def test_rate_limit(client):
    for i in range(60):
        post_notice(client, subject=f"doc:{i}")
    assert post_notice(client, subject="doc:61").status_code == 429


def test_decisions_and_rag_search(client):
    r = client.post(
        "/v1/decisions",
        headers=ADMIN,
        json={
            "subject": "doc:1",
            "check": "notice.timing",
            "decision": "accepted_risk",
            "reason": "Shown on the sign-up screen; verified.",
            "fact_hash": "abc",
        },
    )
    assert r.status_code == 201
    hits = client.post(
        "/v1/rag/search", headers=ADMIN, json={"query": "breach intimation to the Board"}
    ).json()["hits"]
    assert hits[0]["ref"] in ("Section 8(2)", "Rule 4(1)", "Rule 4(2)")


def test_checker_endpoints(client):
    assert client.post("/v1/checker/canary", headers=ADMIN).json()["ok"]
    assert client.get("/health/deep", headers=ADMIN).json()["ok"]
    assert client.get("/v1/checker/checks", headers=ADMIN).json()


# n8n ----------------------------------------------------------------------------------


def signed(body: dict, secret="test-secret", ts=None):
    raw = json.dumps(body).encode()
    ts = str(ts or int(time.time()))
    return raw, {
        "x-grc-timestamp": ts,
        "x-grc-signature": n8n.sign(secret, ts, raw),
        "content-type": "application/json",
    }


HOOK = {
    "type": "evidence.expiring",
    "org_id": "org_a",
    "payload": {"evidence_id": "EV-12", "expires_on": "2026-10-20"},
}


def test_n8n_hook_accepts_signed_requests(client, rt):
    raw, headers = signed(HOOK)
    assert client.post("/v1/hooks/n8n", content=raw, headers=headers).status_code == 202
    assert process_one(rt)["status"] == "passed"  # triage pipeline


@pytest.mark.parametrize("secret,age", [("wrong", 0), ("test-secret", 600)])
def test_n8n_hook_rejects_bad_or_replayed_signatures(client, secret, age):
    raw, headers = signed(HOOK, secret=secret, ts=int(time.time()) - age)
    assert client.post("/v1/hooks/n8n", content=raw, headers=headers).status_code == 401


def test_outbound_events_are_signed_so_n8n_can_verify_them(rt, n8n_sink):
    rt.outbound.emit("run.completed", {"run_id": "r1"})
    req = n8n_sink.requests[-1]
    assert n8n.verify(
        "test-secret", req.headers["x-grc-timestamp"], req.headers["x-grc-signature"], req.content
    )


# Clerk --------------------------------------------------------------------------------

ISSUER = "https://clerk.example.test"


@pytest.fixture(scope="module")
def keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key, **claims):
    now = int(time.time())
    base = {
        "sub": "user_1",
        "iss": ISSUER,
        "iat": now,
        "exp": now + 60,
        "azp": "https://app.example.test",
        "o": {"id": "org_a", "rol": "admin"},
    }
    return jwt.encode({**base, **claims}, key, algorithm="RS256", headers={"kid": "k1"})


def jwks_for(key):
    return SimpleNamespace(
        get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=key.public_key())
    )


def test_clerk_token_is_verified(keypair):
    p = verify_clerk_token(token(keypair), ISSUER, ("https://app.example.test",), jwks_for(keypair))
    assert (p.user_id, p.org_id, p.org_role) == ("user_1", "org_a", "admin")


@pytest.mark.parametrize(
    "claims,code",
    [
        ({"azp": "https://evil.test"}, 401),
        ({"iss": "https://other.test"}, 401),
        ({"exp": int(time.time()) - 120}, 401),
        ({"o": {}}, 403),
    ],
)
def test_clerk_rejects_bad_tokens(keypair, claims, code):
    with pytest.raises(HTTPException) as err:
        verify_clerk_token(
            token(keypair, **claims), ISSUER, ("https://app.example.test",), jwks_for(keypair)
        )
    assert err.value.status_code == code


def test_clerk_token_on_the_api(rt, keypair):
    rt.settings = replace(
        rt.settings,
        auth_mode="clerk",
        clerk_issuer=ISSUER,
        clerk_authorized_parties=("https://app.example.test",),
    )
    app = create_app(rt.settings, rt)
    app.state.jwks = jwks_for(keypair)
    c = TestClient(app)
    assert c.get("/v1/runs").status_code == 401
    assert c.get("/v1/runs", headers={"authorization": f"Bearer {token(keypair)}"}).json() == []


def test_production_refuses_unsafe_settings():
    problems = replace(
        Settings(), environment="production", auth_mode="dev"
    ).validate_for_production()
    assert "GRC_FLOW_AUTH must be 'clerk' in production" in problems
    assert any("PINECONE_API_KEY" in p for p in problems)


@pytest.mark.parametrize("action", ["health", "canary", "sweep"])
def test_n8n_can_schedule_the_checker(client, action):
    raw, headers = signed({"action": action})
    r = client.post("/v1/hooks/n8n/checker", content=raw, headers=headers)
    assert r.status_code == 200
    assert r.json().get("ok", True)


def test_outbound_body_matches_javascript_json_stringify(rt, n8n_sink):
    rt.outbound.emit("run.completed", {"run_id": "r1", "text": "Data Protection Board — India"})
    raw = n8n_sink.requests[-1].content
    # JSON.stringify(JSON.parse(raw)) == raw  <=>  compact separators, unescaped unicode
    assert raw == json.dumps(json.loads(raw), separators=(",", ":"), ensure_ascii=False).encode()
