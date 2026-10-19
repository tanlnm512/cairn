---
type: Tribal-decision
title: Embedding follows reindex commit boundaries
description: Embedding follows reindex commit boundaries
resource: src/cairn/graph/incremental.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:43Z'
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
- date: '2026-10-07T15:04:43Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:43Z'
---

Targeted embedding runs only after the reindex transaction commits, and a failed self-committing batch is settled before rollback.
Why: an embedding commit inside the transaction defeats parse rollback, while an abandoned open transaction can lose buffered rows.
How to apply: keep embedding best-effort and deferred, never fatal, after graph persistence.