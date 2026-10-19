---
type: Tribal-decision
title: Retrieval empty-rate is scoped to semantic searches
description: Retrieval empty-rate is scoped to semantic searches
resource: src/cairn/cli/system/metrics.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:31:07Z'
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
- date: '2026-10-07T16:31:07Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:31:07Z'
---

The metrics empty-result rate divides semantic_search empties by the semantic_backend event total; explore and search_symbols empties are reported only in the per-kind empty_by_kind breakdown and never enter the rate.
Why: only the semantic_search empties share a denominator with the population actually at risk of an empty semantic result, so mixing kinds would dilute or inflate the rate while the per-kind breakdown keeps the rest visible.
How to apply: when a new query kind emits empty_result, add it to empty_by_kind only unless it shares the semantic_backend denominator.