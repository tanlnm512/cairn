---
type: Tribal-decision
title: explore taint warning lives in tools_graph.explore, not queries.explore
description: explore taint warning lives in tools_graph.explore, not queries.explore
resource: src/cairn/mcp_server/tools_graph.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-17T02:39:57Z'
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
- date: '2026-09-17T02:39:57Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T02:39:57Z'
---

The explore taint warning is computed inside cairn.mcp_server.tools_graph.explore from the seed names via intersect_seeds, with the registry built from workspace config (load_config + build_registry); the section renders format_taint_warning output verbatim under '=== Taint paths ===' before the terminal degradation footnote.
Why: D-005 pins explore to compute the intersection from its seeds — the queries.explore result contract must not gain a key, because exact-traffic tests feed the render a five-key stub dict; D-004 pins the footnote as the last line.
How to apply: any new explore section goes in tools_graph.py before the _append_embed_degradation_footnote call and must render off .get()-tolerant or locally-computed data, never a new queries.explore key.