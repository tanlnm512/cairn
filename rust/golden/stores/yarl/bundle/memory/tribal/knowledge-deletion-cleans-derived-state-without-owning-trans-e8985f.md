---
type: Tribal-pattern
title: Knowledge deletion cleans derived state without owning transactions
description: Knowledge deletion cleans derived state without owning transactions
resource: src/cairn/knowledge/store.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:16Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:16Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:16Z'
---

Deletion validates the bundle path, unlinks the document, invalidates cached search state, and removes embeddings on a supplied connection without committing it.
Why: malformed ids must not escape the bundle, deleted docs must stop surfacing, and an owned connection commit can break an in-progress caller transaction.
How to apply: invalidate after unlink and leave transaction boundaries to the caller.