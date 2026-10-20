---
type: Tribal-decision
title: MCP boot warms semantic models in a background thread
description: MCP boot warms semantic models in a background thread
resource: src/cairn/mcp_server/server.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:55Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:35:55Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:55Z'
---

run() calls warm_models_in_background() on the shared stdio+SSE path, after the DB guard and before boot catch-up.
Why: the first semantic_search otherwise pays the full lazy model load (measured around 9.4s, about 5s of it HF Hub metadata round-trips even on cached weights); warming in parallel with reindexing removes that tail without blocking boot; it only ever warms cached weights (never downloads) and is inert for hash/openai backends and a disabled reranker.
How to apply: keep the warm thread logging on the stderr-configured logger — stdout is the stdio JSON-RPC channel; CAIRN_WARM_MODELS remains the kill switch.