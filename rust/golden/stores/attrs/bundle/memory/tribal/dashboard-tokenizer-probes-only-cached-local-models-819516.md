---
type: Tribal-decision
title: Dashboard tokenizer probes only cached local models
description: Dashboard tokenizer probes only cached local models
resource: src/cairn/dashboard/tokenizer.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:47Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:47Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:47Z'
---

Tokenizer resolution imports the local model stack lazily and constructs the tokenizer with local_files_only; any unavailable tokenizer degrades to heuristic estimation.
Why: opening the dashboard must not pay embedding imports or trigger network model downloads, and semantic dependencies being absent is normal.
How to apply: keep the lazy config-aware probe and share its resolved mode rather than probing per estimate.