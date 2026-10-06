"""The RAG layer as tools for GRC-Ai's analyst agent (`grc_agent.agent.Agent`).

    from grc_agent.agent import Agent
    from grc_agent.tools import BASE_TOOLS
    agent = Agent(tools=BASE_TOOLS + knowledge_tools(rt.rag, org_id="org_123"))

The org id is bound when the tools are built (from the signed-in user's Clerk org),
never taken from the model, so the analyst can only read its own org's documents.
"""

from __future__ import annotations

from typing import Any

from grc_agent.tools import Tool, ToolError

from grc_flow.rag.layer import RagLayer


def knowledge_tools(rag: RagLayer, org_id: str | None = None) -> list[Tool]:
    scopes = ["law", "guidance"] + (["org"] if org_id else [])

    def search_knowledge(query: str, scope: list[str]) -> dict[str, Any]:
        wanted = tuple(s for s in scope if s in scopes) or ("law", "guidance")
        hits = rag.search(query, k=6, scope=wanted, org_id=org_id)
        return {
            "kind": "knowledge",
            "note": "Only items with authority 'law' may be cited as law. Guidance explains; "
            "org items are the client's own documents.",
            "results": [h.to_dict(max_chars=1200) for h in hits],
        }

    def get_law_provision(ref: str) -> dict[str, Any]:
        chunks = rag.provision(ref)
        if not chunks:
            raise ToolError(f"No provision matches {ref!r}. Use a form like 'Section 8(6)'.")
        return {
            "kind": "provision",
            "ref": ref,
            "text": [{"ref": c.ref, "heading": c.heading, "text": c.text} for c in chunks[:6]],
        }

    return [
        Tool(
            name="search_knowledge",
            description=(
                "Search the DPDPA knowledge base. Scopes: 'law' (DPDP Act and Rules, the only "
                "citable source), 'guidance' (obligations register: evidence to request and "
                "remediation; explanatory notes)"
                + (", 'org' (this client's own uploaded documents)" if org_id else "")
                + ". Use it before answering any question about obligations or the client's "
                "documents."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for."},
                    "scope": {"type": "array", "items": {"type": "string", "enum": scopes}},
                },
                "required": ["query", "scope"],
                "additionalProperties": False,
            },
            handler=search_knowledge,
        ),
        Tool(
            name="get_law_provision",
            description="Exact text of a DPDP Act or Rules provision, e.g. 'Section 8(6)', "
            "'Rule 7'. Use it before quoting or citing a provision.",
            input_schema={
                "type": "object",
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
                "additionalProperties": False,
            },
            handler=get_law_provision,
        ),
    ]
