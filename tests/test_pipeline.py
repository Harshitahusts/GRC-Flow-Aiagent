from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from grc_flow.agents.checker import Checker
from grc_flow.agents.extractor import ClaudeExtractor, ExtractionError
from grc_flow.agents.facts import Fact
from grc_flow.agents.rules import evaluate
from grc_flow.canaries import COMPLETE_NOTICE, INCOMPLETE_NOTICE
from grc_flow.pipeline.runner import run_event
from grc_flow.pipeline.worker import process_one
from tests.conftest import notice_event


def statuses(run: dict) -> dict[str, str]:
    return {f["check_name"]: f["status"] for f in run["findings"]}


def test_rag_retrieves_the_notice_section(rt):
    hits = rt.rag.search("what must a notice tell the person about purpose", 3, scope=("law",))
    assert hits[0].ref.startswith("Section 5")
    assert {"bm25", "vector"} <= set(hits[0].via)


def test_complete_notice_is_compliant_and_every_step_is_recorded(rt):
    event = notice_event(
        rt, COMPLETE_NOTICE, facts={"is_given_before_or_with_consent_request": "yes"}
    )
    rt.store.add_event(event)
    run = run_event(rt, event)
    assert run["status"] == "passed"
    assert set(statuses(run).values()) == {"compliant"}
    assert [s["name"] for s in run["steps"]] == [
        "load_document",
        "index_document",
        "retrieve_knowledge",
        "extract_facts",
        "ground_facts",
        "apply_rules",
        "apply_memory",
        "verify_findings",
        "persist",
    ]
    assert all(f["citations"] for f in run["findings"])
    # The run log never stores the document itself.
    assert COMPLETE_NOTICE[:40] not in json.dumps(run["steps"])


def test_incomplete_notice_gives_gaps_and_unknown_process_fact_needs_review(rt):
    event = notice_event(rt, INCOMPLETE_NOTICE)
    rt.store.add_event(event)
    run = run_event(rt, event)
    s = statuses(run)
    assert s["notice.describes_data"] == "gap"
    assert s["notice.board_complaint"] == "gap"
    assert s["notice.timing"] == "needs_human_review"
    assert run["status"] == "passed"  # the flow worked; findings carry the review need


def test_checker_downgrades_a_fabricated_quote():
    facts = [Fact("describes_purpose", "yes", "We use data only to deliver orders.", "high")]
    facts, issues = Checker().ground_facts("We collect your name.", facts)
    assert facts[0].value == "unknown" and issues[0].code == "quote_not_found"


def test_checker_accepts_quotes_with_different_whitespace_and_curly_quotes():
    doc = "We use your  data\nfor “delivery” only."
    facts = [Fact("describes_purpose", "yes", 'We use your data for "delivery" only.', "high")]
    facts, issues = Checker().ground_facts(doc, facts)
    assert facts[0].value == "yes" and not issues


def test_unresolvable_citation_blocks_the_finding():
    facts = [Fact("describes_complaint_method_to_board", "yes", "x", "high")]
    findings = evaluate("doc", facts, resolves=lambda ref: ref == "Section 5(1)")
    board = next(f for f in findings if f["check"] == "notice.board_complaint")
    issues = Checker().verify_findings([board], resolves=lambda ref: False)
    assert board["status"] == "needs_human_review"
    assert {i.code for i in issues} == {"citation_unresolved"}


def test_rules_never_decide_on_low_confidence_facts():
    facts = [Fact("describes_rights_exercise_method", "no", "", "low")]
    finding = next(
        f for f in evaluate("doc", facts, lambda r: True) if f["check"] == "notice.rights_exercise"
    )
    assert finding["status"] == "needs_human_review" and finding["citations"] == []


def test_accepted_risk_is_remembered_until_the_facts_change(rt):
    first = notice_event(rt, INCOMPLETE_NOTICE)
    rt.store.add_event(first)
    run = run_event(rt, first)
    gap = next(f for f in run["findings"] if f["check_name"] == "notice.board_complaint")
    rt.store.add_decision(
        {
            "org_id": "org_a",
            "subject": "doc:1",
            "check": gap["check_name"],
            "decision": "accepted_risk",
            "reason": "Legal approved for now.",
            "decided_by": "user_1",
            "fact_hash": gap["fact_hash"],
        }
    )

    again = notice_event(rt, INCOMPLETE_NOTICE + " ")  # same facts, new event
    rt.store.add_event(again)
    remembered = next(
        f for f in run_event(rt, again)["findings"] if f["check_name"] == "notice.board_complaint"
    )
    assert remembered["memory"]["decision"] == "accepted_risk"

    changed = notice_event(
        rt, INCOMPLETE_NOTICE + " You can complain to the Data Protection Board of India."
    )
    rt.store.add_event(changed)
    resurfaced = next(
        f for f in run_event(rt, changed)["findings"] if f["check_name"] == "notice.board_complaint"
    )
    assert resurfaced["status"] == "compliant"


def test_duplicate_submissions_are_not_queued_twice(rt):
    payload = {"doc_type": "privacy_notice", "subject": "doc:9", "text": COMPLETE_NOTICE}
    _, first = rt.submit(rt.make_event("document.uploaded", "org_a", "web", payload, None))
    _, second = rt.submit(rt.make_event("document.uploaded", "org_a", "web", payload, None))
    assert (first, second) == (True, False)
    assert rt.kv.queue_depth() == 1


def test_worker_runs_queued_events_and_notifies_n8n(rt, n8n_sink):
    rt.submit(notice_event(rt, COMPLETE_NOTICE))
    result = process_one(rt)
    assert result["status"] == "passed"
    assert process_one(rt) is None
    sent = n8n_sink.bodies()
    assert sent[-1]["type"] == "run.completed" and sent[-1]["data"]["run_id"] == result["run_id"]


def test_worker_lock_stops_double_processing(rt):
    event = notice_event(rt, COMPLETE_NOTICE)
    rt.submit(event)
    rt.kv.enqueue({"event_id": event["id"]})  # a redelivery
    assert "run_id" in process_one(rt)
    assert process_one(rt)["skipped"] == "already being processed"


def test_stale_running_runs_are_requeued(rt):
    event = notice_event(rt, COMPLETE_NOTICE)
    rt.store.add_event(event)
    run_id = rt.store.start_run(event["id"], "notice_review")
    with rt.store.conn() as c:
        c.execute(
            "UPDATE flow_runs SET started_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (run_id,)
        )
    assert rt.checker.sweep_stale_runs(rt) == 1
    assert rt.store.get_run(run_id)["status"] == "abandoned"
    assert rt.kv.dequeue() == {"event_id": event["id"]}


def test_canaries_pass_and_health_is_green(rt):
    assert rt.checker.run_canaries(rt)["ok"]
    health = rt.checker.health(rt)
    assert health["ok"], health


def test_health_reports_a_missing_corpus(rt, n8n_sink):
    rt.rag = None
    health = rt.checker.health(rt)
    assert not health["ok"] and not health["services"]["corpus"]["ok"]
    assert n8n_sink.bodies()[-1]["type"] == "checker.alert"


# Claude extractor ---------------------------------------------------------------------


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def response(data=None, stop="end_turn"):
    text = json.dumps(data) if data is not None else "not json"
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])


def all_facts(value="yes", quote="We collect your name."):
    names = [
        "describes_personal_data",
        "describes_purpose",
        "describes_rights_exercise_method",
        "describes_complaint_method_to_board",
        "available_in_english_or_eighth_schedule_language",
    ]
    return {"facts": {n: {"value": value, "quote": quote, "confidence": "high"} for n in names}}


def test_claude_extractor_uses_structured_output_and_retries_once():
    client = FakeClient([response(None), response(all_facts())])
    facts = ClaudeExtractor("claude-opus-5-5", "high", client).extract("We collect your name.", [])
    assert len(facts) == 5 and all(f.source == "llm" for f in facts)
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"


def test_claude_refusal_fails_the_run_and_alerts(rt, n8n_sink):
    rt.extractor = ClaudeExtractor(
        "claude-opus-5-5", "high", FakeClient([response(stop="refusal")])
    )
    event = notice_event(rt, COMPLETE_NOTICE)
    rt.store.add_event(event)
    run = run_event(rt, event)
    assert run["status"] == "failed" and "ExtractionError" in run["error"]
    assert run["steps"][-1] == {**run["steps"][-1], "name": "extract_facts", "status": "failed"}
    assert n8n_sink.bodies()[-1]["type"] == "run.failed"


def test_claude_quotes_are_still_checked(rt):
    rt.extractor = ClaudeExtractor(
        "claude-opus-5-5", "high", FakeClient([response(all_facts(quote="Invented sentence."))])
    )
    event = notice_event(rt, COMPLETE_NOTICE)
    rt.store.add_event(event)
    run = run_event(rt, event)
    assert set(statuses(run).values()) == {"needs_human_review"}


def test_oversized_documents_are_refused_not_truncated():
    with pytest.raises(ExtractionError):
        ClaudeExtractor("m", "high", FakeClient([])).extract("x" * 400_001, [])


def test_local_index_fills_itself_from_the_built_corpus(settings):
    from grc_flow.adapters.vectors import MemoryIndex
    from grc_flow.cli import rag_init
    from grc_flow.runtime import Runtime

    rag_init(settings, MemoryIndex())  # rag-init in "another process"
    rt = Runtime.build(settings)  # fresh process: new, empty memory index
    assert rt.index.count("law") == len(rt.rag.law_chunks) > 0
    assert rt.index.count("guidance") == len(rt.rag.guidance_chunks) > 0
    assert rt.checker.health(rt)["services"]["vectors"]["ok"]
