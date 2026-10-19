---
type: Tribal-decision
title: Edge resolution values encode resolver trust
description: Edge resolution values encode resolver trust
resource: src/cairn/graph/schema.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:46Z'
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
- date: '2026-10-07T14:52:46Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:46Z'
---

edges.resolution uses exact for one resolved candidate, ambiguous for deliberately unresolved multiple candidates, and unresolved for no candidate.
Why: Why: consumers must distinguish trusted structural edges from name-only matches instead of treating every stored edge alike.
How to apply: How to apply: preserve these semantics when adding importers or resolution tiers.