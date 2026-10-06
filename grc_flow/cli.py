"""grc-flow command line.

grc-flow rag-init          ingest the corpus and load it into the vector index
grc-flow serve             run the API (uvicorn)
grc-flow worker [--once]   process queued events
grc-flow check             run the checker's health probes
grc-flow canary            run the canaries through the real pipeline
grc-flow submit FILE       queue an event from a JSON file {type, org_id, payload}
grc-flow demo              offline end-to-end demo on the fictional fixture corpus
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from grc_flow.config import Settings


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def rag_init(settings: Settings, index):
    """Ingest the corpus, chunk law + guidance, and sync both namespaces."""
    from grc_flow.rag.init import build_corpus
    from grc_flow.rag.layer import RagLayer

    corpus, version = build_corpus(settings.corpus_dir)
    rag = RagLayer(index, corpus, version)
    info = index.ensure()
    report = rag.sync_knowledge(max_tokens=info.get("max_tokens"))
    return rag, {"index": info, "knowledge_version": rag.version, "namespaces": report}


def cmd_rag_init(settings: Settings, _args) -> int:
    from grc_flow.adapters.vectors import make_index
    from grc_flow.rag.init import RagInitError

    try:
        _rag, report = rag_init(settings, make_index(settings))
    except (RagInitError, FileNotFoundError, ValueError) as exc:
        print(f"rag-init failed: {exc}", file=sys.stderr)
        return 1
    _print(report)
    return 0


def cmd_serve(settings: Settings, args) -> int:
    import uvicorn

    from grc_flow.api import create_app

    app = create_app(settings)
    if args.with_worker:
        # Single-box mode: the worker shares this process (and its local queue). With
        # Upstash configured, run `grc-flow worker` processes separately instead.
        import threading

        from grc_flow.pipeline.worker import run_forever

        threading.Thread(target=run_forever, args=(app.state.rt,), daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True)
    return 0


def cmd_worker(settings: Settings, args) -> int:
    from grc_flow.pipeline.worker import process_one, run_forever
    from grc_flow.runtime import Runtime

    rt = Runtime.build(settings)
    if not args.once:
        run_forever(rt)
        return 0
    while (result := process_one(rt)) is not None:
        _print(result)
    return 0


def cmd_check(settings: Settings, _args) -> int:
    from grc_flow.runtime import Runtime

    rt = Runtime.build(settings)
    report = rt.checker.health(rt)
    _print(report)
    return 0 if report["ok"] else 2


def cmd_canary(settings: Settings, _args) -> int:
    from grc_flow.runtime import Runtime

    rt = Runtime.build(settings)
    report = rt.checker.run_canaries(rt)
    _print(report)
    return 0 if report["ok"] else 2


def cmd_submit(settings: Settings, args) -> int:
    from grc_flow.runtime import Runtime

    data = json.loads(Path(args.file).read_text("utf-8"))
    rt = Runtime.build(settings)
    event = rt.make_event(
        data["type"], data["org_id"], "web", data["payload"], data.get("idempotency_key")
    )
    event_id, accepted = rt.submit(event)
    _print({"event_id": event_id, "accepted": accepted})
    return 0


def cmd_demo(settings: Settings, _args) -> int:
    """Everything offline: fixture corpus, in-memory index and queue, offline extractor."""
    from grc_flow.adapters.kv import MemoryKV
    from grc_flow.adapters.vectors import MemoryIndex
    from grc_flow.canaries import COMPLETE_NOTICE, INCOMPLETE_NOTICE
    from grc_flow.pipeline.worker import process_one
    from grc_flow.runtime import Runtime

    fixtures = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "corpus"
    settings = replace(
        settings,
        ai_mode="offline",
        corpus_dir=str(fixtures),
        sqlite_path="var/demo.db",
        auth_mode="dev",
        environment="development",
    )
    Path(settings.sqlite_path).unlink(missing_ok=True)  # a fresh demo every time
    index = MemoryIndex()
    rag, report = rag_init(settings, index)
    print("knowledge:", {ns: r["chunks"] for ns, r in report["namespaces"].items()})
    rt = Runtime.build(settings, index=index, kv=MemoryKV(), rag=rag)
    for subject, text in (
        ("doc:retail-notice", COMPLETE_NOTICE),
        ("doc:games-notice", INCOMPLETE_NOTICE),
    ):
        rt.submit(
            rt.make_event(
                "document.uploaded",
                "org_demo",
                "web",
                {"doc_type": "privacy_notice", "subject": subject, "text": text},
                None,
            )
        )
    while (result := process_one(rt)) is not None:
        run = rt.store.get_run(result["run_id"])
        print(f"\n{run['id']}  {run['status']}")
        for s in run["steps"]:
            print(f"  {s['seq']}. {s['name']:<20} {s['status']:<6} {s['ms']:>4} ms")
        for f in run["findings"]:
            print(f"  - {f['check_name']:<24} {f['status']:<20} {', '.join(f['citations'])}")
    _print(rt.checker.health(rt)["services"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grc-flow")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rag-init")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    serve.add_argument("--with-worker", action="store_true", help="run the worker in-process")
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true", help="drain the queue, then exit")
    sub.add_parser("check")
    sub.add_parser("canary")
    submit = sub.add_parser("submit")
    submit.add_argument("file")
    sub.add_parser("demo")
    args = parser.parse_args(argv)
    handler = {
        "rag-init": cmd_rag_init,
        "serve": cmd_serve,
        "worker": cmd_worker,
        "check": cmd_check,
        "canary": cmd_canary,
        "submit": cmd_submit,
        "demo": cmd_demo,
    }[args.cmd]
    return handler(Settings.from_env(), args)


if __name__ == "__main__":
    sys.exit(main())
