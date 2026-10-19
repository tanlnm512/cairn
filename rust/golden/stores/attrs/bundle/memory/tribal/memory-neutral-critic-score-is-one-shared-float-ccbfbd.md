---
type: Tribal-decision
title: Memory neutral critic score is one shared float
description: Memory neutral critic score is one shared float
resource: src/cairn/memory/promotion.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:31Z'
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
- date: '2026-10-07T14:39:31Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:31Z'
---

When no LLM critic is available, capture and score_memory use the same DEFAULT_CRITIC_SCORE float.
Why: compute_score has no None handling, and split neutral defaults make paths disagree on memory quality.
How to apply: change the neutral default once and update both scoring and capture tests together.