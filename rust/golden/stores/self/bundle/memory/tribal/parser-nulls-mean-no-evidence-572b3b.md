---
type: Tribal-decision
title: Parser nulls mean no evidence
description: Parser nulls mean no evidence
resource: src/cairn/graph/schema.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:47Z'
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
- date: '2026-10-07T14:52:47Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:47Z'
---

Import local aliases and symbol arity remain null when the parser has no evidence rather than defaulting to a guessed alias or arity.
Why: Why: null distinguishes absent evidence from a real value and prevents fabricated precision in ranking or resolution.
How to apply: How to apply: new parser projections must use null for no evidence and document their legacy fallback separately.