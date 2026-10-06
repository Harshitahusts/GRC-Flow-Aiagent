from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from grc_agent.tools import ToolError

from grc_flow.adapters.vectors import PineconeIndex
from grc_flow.api import create_app
from grc_flow.canaries import COMPLETE_NOTICE
from grc_flow.pipeline.runner import run_event
from grc_flow.rag.chunking import (
    MAX_TOKENS,
    chunk_document,
    chunk_law,
    estimate_tokens,
    split,
)
from grc_flow.rag.layer import NS_GUIDANCE, org_namespace
from grc_flow.rag.tools import knowledge_tools
from tests.conftest import notice_event

LONG_DOC = "\n\n".join(
    f"Section {p} of our policy. "
    + " ".join(f"We keep record {p}.{s} for the purpose of billing and support." for s in range(9))
    for p in range(12)
)


# Chunking ------------------------------------------------------------------------------


def test_every_knowledge_chunk_fits_the_embedding_budget(rt):
    chunks = rt.rag.law_chunks + rt.rag.guidance_chunks
    assert chunks and max(estimate_tokens(c.text) for c in chunks) <= MAX_TOKENS


def test_chunk_offsets_point_back_to_the_exact_source_text(rt):
    for c in rt.rag.law_chunks:
        provision = rt.rag.corpus.provision(c.ref)[0]
        assert provision.text[c.char_start : c.char_end] == c.body
    doc = chunk_document("doc:long", LONG_DOC, "Retention policy", budget=120)
    assert all(LONG_DOC[c.char_start : c.char_end] == c.body for c in doc)


def test_split_covers_all_text_and_overlaps_sentences():
    spans = split(LONG_DOC, budget=150, overlap=30)
    assert len(spans) > 3
    assert all(estimate_tokens(LONG_DOC[s:e]) <= 150 for s, e in spans)
    covered = set()
    for s, e in spans:
        covered.update(range(s, e))
    assert all(i in covered for i, ch in enumerate(LONG_DOC) if not ch.isspace())
    assert any(spans[i + 1][0] < spans[i][1] for i in range(len(spans) - 1))


def test_long_provision_splits_at_clause_lines_and_keeps_its_citation():
    text = "A fiduciary shall—\n" + "\n".join(
        f"({chr(97 + i)}) clause {i} " + "words " * 40 for i in range(8)
    )
    provision = SimpleNamespace(
        key="section 9(1)", ref="Section 9(1)", heading="Duties", source="act.txt", text=text
    )
    chunks = chunk_law([provision], budget=150)
    assert len(chunks) > 1 and {c.ref for c in chunks} == {"Section 9(1)"}
    assert all(re.match(r"^(\([a-h]\)|A fiduciary)", c.body) for c in chunks)
    assert all(c.text.startswith("Section 9(1). Duties") for c in chunks)


def test_hindi_text_is_chunked_on_the_danda():
    text = "हम आपका नाम और फ़ोन नंबर एकत्र करते हैं। " * 60
    chunks = chunk_document("doc:hi", text, budget=100)
    assert len(chunks) > 1
    assert all(c.body.endswith("।") for c in chunks[:-1])


# Indexing and search -------------------------------------------------------------------


def test_knowledge_namespaces_are_filled(rt):
    assert rt.index.count("law") == len(rt.rag.law_chunks)
    assert rt.index.count(NS_GUIDANCE) == len(rt.rag.guidance_chunks) > 20
    assert rt.checker.health(rt)["services"]["vectors"]["ok"]


def test_guidance_answers_what_evidence_to_request(rt):
    hits = rt.rag.search("what evidence to request for breach notification", 5, ("guidance",))
    assert hits and all(h.authority == "guidance" for h in hits)
    assert any(h.source_type == "register" for h in hits)


def test_reindexing_a_document_replaces_its_old_chunks(rt):
    first = rt.rag.index_document("org_a", "doc:policy", LONG_DOC)
    assert first["chunks"] > 3
    second = rt.rag.index_document("org_a", "doc:policy", "We collect your email. Short now.")
    assert second["chunks"] == 1 and second["deleted"] == first["chunks"] - 1
    assert rt.index.count(org_namespace("org_a")) == 1
    assert rt.rag.delete_document("org_a", "doc:policy") == 1


def test_org_documents_never_leak_across_orgs(rt):
    rt.rag.index_document("org_a", "doc:secret", "Our vendor Acme Payroll stores salary data.")
    mine = rt.rag.search("salary vendor", 3, ("org",), org_id="org_a")
    theirs = rt.rag.search("salary vendor", 3, ("org",), org_id="org_b")
    assert mine and mine[0].doc_id == "doc:secret" and theirs == []
    with pytest.raises(ValueError):
        rt.rag.search("salary", 3, ("org",))


def test_records_from_an_old_knowledge_version_are_ignored_and_flagged(rt):
    stale = {**rt.index.namespaces[NS_GUIDANCE]["oblig:OBL-001"], "version": "old"}
    rt.index.upsert(NS_GUIDANCE, [{**stale, "_id": "oblig:OBL-001"}])
    hits = rt.rag.search("notice describing the personal data and purpose", 10, ("guidance",))
    assert "oblig:OBL-001" not in [h.id for h in hits]
    for rid, rec in list(rt.index.namespaces[NS_GUIDANCE].items()):
        rt.index.upsert(NS_GUIDANCE, [{**rec, "_id": rid, "version": "old"}])
    vectors = rt.checker.health(rt)["services"]["vectors"]
    assert not vectors["ok"] and not vectors[NS_GUIDANCE]["version_matches"]


def test_sync_removes_records_that_no_longer_exist(rt):
    rt.index.upsert(NS_GUIDANCE, [{"_id": "guide:removed-page#0", "chunk_text": "old"}])
    report = rt.rag.sync_knowledge()
    assert report[NS_GUIDANCE]["deleted_stale"] == 1
    assert "guide:removed-page#0" not in rt.index.namespaces[NS_GUIDANCE]


def test_sync_refuses_chunks_the_model_would_truncate(rt):
    with pytest.raises(ValueError, match="truncated"):
        rt.rag.sync_knowledge(max_tokens=50)


def test_the_pipeline_indexes_the_notice_into_the_org_namespace(rt):
    event = notice_event(rt, COMPLETE_NOTICE, subject="doc:notice-7", title="Privacy notice")
    rt.store.add_event(event)
    run = run_event(rt, event)
    step = next(s for s in run["steps"] if s["name"] == "index_document")
    assert step["detail"]["namespace"] == org_namespace("org_a") and step["detail"]["chunks"] >= 1
    hits = rt.rag.search("grievance officer", 3, ("org",), org_id="org_a")
    assert hits[0].doc_id == "doc:notice-7" and hits[0].title == "Privacy notice"
    knowledge = next(s for s in run["steps"] if s["name"] == "retrieve_knowledge")["detail"]
    assert knowledge["refs"] and knowledge["guidance"]


# Analyst tools and API -----------------------------------------------------------------


def test_analyst_tools_are_bound_to_one_org(rt):
    rt.rag.index_document("org_a", "doc:dpa", "Data processing agreement with Acme Cloud.")
    rt.rag.index_document("org_b", "doc:other", "Data processing agreement with Other Cloud.")
    tools = {t.name: t for t in knowledge_tools(rt.rag, org_id="org_a")}
    out = tools["search_knowledge"].handler(query="data processing agreement", scope=["org"])
    assert {r["doc_id"] for r in out["results"]} == {"doc:dpa"}
    assert tools["get_law_provision"].handler(ref="Section 8(2)")["text"]
    with pytest.raises(ToolError):
        tools["get_law_provision"].handler(ref="Section 99")
    no_org = {t.name: t for t in knowledge_tools(rt.rag)}
    schema = no_org["search_knowledge"].input_schema["properties"]["scope"]["items"]["enum"]
    assert "org" not in schema


def test_api_search_scopes_and_document_delete(rt):
    client = TestClient(create_app(rt.settings, rt))
    headers = {"x-dev-user": "u1", "x-dev-org": "org_a"}
    rt.rag.index_document("org_a", "doc:hr", "Employee health records are kept for 7 years.")
    r = client.post(
        "/v1/rag/search",
        headers=headers,
        json={"query": "health records retention", "scope": ["org"]},
    )
    assert r.json()["hits"][0]["doc_id"] == "doc:hr"
    bad = client.post("/v1/rag/search", headers=headers, json={"query": "x y", "scope": ["web"]})
    assert bad.status_code == 422
    assert client.delete("/v1/documents/doc:hr", headers=headers).json()["deleted_chunks"] == 1


# Pinecone adapter against a fake client ------------------------------------------------


class FakePineconeIndex:
    def __init__(self):
        self.upserts, self.deletes, self.searches = [], [], []

    def upsert_records(self, namespace, records):
        self.upserts.append((namespace, len(records)))

    def search(self, namespace, top_k, inputs, fields):
        self.searches.append((namespace, top_k, inputs, tuple(fields)))
        hit = SimpleNamespace(
            id="law:section_5(1)#0", score=0.9, fields={"ref": "Section 5(1)", "version": "v"}
        )
        return SimpleNamespace(result=SimpleNamespace(hits=[hit]))

    def list(self, prefix, namespace):
        yield SimpleNamespace(vectors=[SimpleNamespace(id=f"{prefix}{i}") for i in range(2)])
        yield SimpleNamespace(vectors=[SimpleNamespace(id=f"{prefix}2")])

    def delete(self, ids, namespace):
        self.deletes.append((namespace, len(ids)))


def fake_pinecone() -> tuple[PineconeIndex, FakePineconeIndex]:
    fake = FakePineconeIndex()
    pc = PineconeIndex.__new__(PineconeIndex)
    pc.index_name, pc.embed_model, pc.cloud, pc.region = "grc", "multilingual-e5-large", "aws", "x"
    pc._index = fake
    pc._pc = SimpleNamespace(
        indexes=SimpleNamespace(exists=lambda name: True),
        index=lambda name: fake,
        inference=SimpleNamespace(
            model=SimpleNamespace(get=lambda m: SimpleNamespace(max_sequence_length=507))
        ),
    )
    return pc, fake


def test_pinecone_adapter_batches_and_uses_namespaces():
    pc, fake = fake_pinecone()
    assert pc.ensure()["max_tokens"] == 507
    assert pc.upsert("law", [{"_id": str(i), "chunk_text": "x"} for i in range(200)]) == 200
    assert fake.upserts == [("law", 90), ("law", 90), ("law", 20)]
    hits = pc.search("org-org_a", "notice", 4)
    assert fake.searches[0][:3] == ("org-org_a", 4, {"text": "notice"})
    assert hits[0].fields["ref"] == "Section 5(1)"
    assert pc.list_ids("org-org_a", "doc:x#") == ["doc:x#0", "doc:x#1", "doc:x#2"]
    pc.delete_ids("org-org_a", [str(i) for i in range(2500)])
    assert fake.deletes == [("org-org_a", 1000), ("org-org_a", 1000), ("org-org_a", 500)]


def test_rag_init_refuses_a_file_whose_checksum_changed(corpus_dir):
    import json

    from grc_flow.rag.init import RagInitError, build_corpus

    manifest = json.loads((corpus_dir / "manifest.json").read_text())
    manifest["sources"][0]["sha256"] = "0" * 64
    (corpus_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RagInitError, match="sha256"):
        build_corpus(corpus_dir)
