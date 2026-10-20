---
type: Tribal-pattern
title: Memory embedding batches isolate unread concepts
description: Memory embedding batches isolate unread concepts
resource: src/cairn/graph/embeddings.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:45Z'
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
- date: '2026-10-07T14:52:45Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:45Z'
---

Selected memory concepts are read first, all surviving chunks are embedded in one backend call, and each concept is individually deleted and reinserted so variable chunk counts replace old rows.
Why: Why: batching amortizes the expensive call while deleted or moved concepts cannot abort otherwise embeddable work.
How to apply: How to apply: resolve readability before embedding and retain per-concept replacement rather than a fixed chunk-index upsert.