---
type: Tribal-pattern
title: Manual memory sources bypass freshness decay
description: Manual memory sources bypass freshness decay
resource: src/cairn/memory/scoring.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:32Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T14:39:32Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:32Z'
---

Freshness decays exponentially with a memory-type-specific half-life, while concepts whose doc_source is manual stay fully fresh.
Why: human-authored memory is durable engineering knowledge rather than session data that should age like captured raw memories.
How to apply: treat new human-authored sources as non-decaying and express type windows as the age at which freshness reaches one half.