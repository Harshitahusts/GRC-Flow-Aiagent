from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from grc_agent.web import pg

from grc_flow.adapters.kv import MemoryKV
from grc_flow.adapters.n8n import Outbound
from grc_flow.adapters.vectors import MemoryIndex
from grc_flow.config import Settings
from grc_flow.rag.init import init_rag
from grc_flow.rag.retrieve import HybridRetriever
from grc_flow.runtime import Runtime

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    target = tmp_path / "corpus"
    shutil.copytree(FIXTURES / "corpus", target, ignore=shutil.ignore_patterns("build"))
    return target


@pytest.fixture
def settings(tmp_path: Path, corpus_dir: Path) -> Settings:
    url = os.getenv("GRC_FLOW_TEST_DATABASE_URL", "")
    return replace(
        Settings(),
        sqlite_path=str(tmp_path / "flow.db"),
        corpus_dir=str(corpus_dir),
        ai_mode="offline",
        auth_mode="dev",
        n8n_webhook_secret="test-secret",
        n8n_outbound_url="https://n8n.test/webhook/grc-flow",
        # Set GRC_FLOW_TEST_DATABASE_URL to run the suite on PostgreSQL (as CI does),
        # one schema per test.
        database_url=pg.with_schema(url, f"t_{uuid.uuid4().hex[:12]}") if url else "",
    )


class Captured:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True})

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def n8n_sink() -> Captured:
    return Captured()


@pytest.fixture
def rt(settings: Settings, n8n_sink: Captured) -> Runtime:
    index = MemoryIndex()
    corpus, report = init_rag(settings.corpus_dir, index)
    client = httpx.Client(transport=httpx.MockTransport(n8n_sink.handler))
    return Runtime.build(
        settings,
        index=index,
        kv=MemoryKV(),
        retriever=HybridRetriever(corpus, index, report.corpus_version),
        corpus_version=report.corpus_version,
        outbound=Outbound(settings.n8n_outbound_url, settings.n8n_webhook_secret, client),
    )


def notice_event(rt, text: str, subject: str = "doc:1", org: str = "org_a", **payload):
    return rt.make_event(
        "document.uploaded",
        org,
        "web",
        {"doc_type": "privacy_notice", "subject": subject, "text": text, **payload},
        None,
    )
