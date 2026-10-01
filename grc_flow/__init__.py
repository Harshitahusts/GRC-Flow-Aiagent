"""GRC-Flow: the backend AI compliance pipeline for GRC-Ai.

Events (from the GRC-Ai web app, n8n, or the checker's canaries) go onto a queue.
A worker runs each one through a fixed pipeline: retrieve the law, extract facts
with Claude, decide compliance with deterministic rules, verify everything with
the checker agent, then store the finding and notify n8n.
"""

__version__ = "0.1.0"
