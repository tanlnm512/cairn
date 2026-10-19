---
type: Tribal-decision
title: Memory capture is the redaction and supersession chokepoint
description: Memory capture is the redaction and supersession chokepoint
resource: src/cairn/memory/promotion.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:28Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T14:39:28Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:28Z'
---

capture_memory and evolve_memory scrub both title and body before scoring or storage; validate stance before writes; seed reference baselines; and supersede only a latest memory of the same type by exact title or semantic cosine over threshold, chaining bundle-relative ids.
Why: CLI, MCP, and hook callers converge here, while titles persist verbatim into descriptions and filenames and supersession chains must survive workspace moves.
How to apply: route new memory writers through this boundary and keep privacy, stance validation, reference baselines, and relative supersedes ids covered together.