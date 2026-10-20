---
type: Tribal-decision
title: term_df rebuild prefers FTS vocabulary
description: term_df rebuild prefers FTS vocabulary
resource: src/cairn/graph/schema.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:51Z'
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
- date: '2026-10-07T14:52:51Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:51Z'
---

term_df rebuild reads exact per-term document counts from FTS5 vocabulary row mode, falls back to a deterministic unicode61 aggregate scan when vocabulary is unavailable, and commits the result.
Why: Why: vocabulary counts are authoritative and cheaper, while the fallback keeps rebuilds working on unusual SQLite builds.
How to apply: How to apply: keep both paths deterministic from database contents and preserve unicode61-compatible tokenization.