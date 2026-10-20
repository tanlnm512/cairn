---
type: Tribal-decision
title: file_api synthesizes signatures from stored rows
description: file_api synthesizes signatures from stored rows
resource: src/cairn/graph/file_api.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-15T18:02:40Z'
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
- date: '2026-09-15T18:02:40Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T18:02:40Z'
---

The file symbol surface reads symbols/files rows only, renders signatures from stored parameters and return_type, omits absent pieces, and uses null when neither signal exists.\nWhy: Stored body text is indexing context, not API output, and adding signature storage would duplicate parser-derived evidence.\nHow to apply: Query stored rows without selecting body; extend the read-only service rather than the builder for file surface changes.