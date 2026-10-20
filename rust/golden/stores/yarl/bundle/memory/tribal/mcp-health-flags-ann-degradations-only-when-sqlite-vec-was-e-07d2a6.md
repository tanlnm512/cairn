---
type: Tribal-decision
title: MCP health flags ANN degradations only when sqlite-vec was expected
description: MCP health flags ANN degradations only when sqlite-vec was expected
resource: src/cairn/mcp_server/_server_core.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:25Z'
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
- date: '2026-10-07T15:35:25Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:25Z'
---

ANN enters the degradations list only when the configured backend is sqlite-vec; an explicit CAIRN_ANN_BACKEND=off is an informed choice. Within sqlite-vec: unavailable means expected-but-load-failed; no_index means embeddings populated but no vec0 table (semantic queries silently brute-force); stale means index row count drifts from embed count, direction-aware (unindexed rows lose recall; stale extra rows can pair a REUSED rowid with an unrelated vector).
Why: mirrors ann_index.ann_backend_enabled() and the doctor ANN checks so the two surfaces agree.
How to apply: extend both surfaces together when ANN states change.