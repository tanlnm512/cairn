---
type: Tribal-pattern
title: Memory reference analytics are scrubbed and batched
description: Memory reference analytics are scrubbed and batched
resource: src/cairn/memory/promotion.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:29Z'
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
- date: '2026-10-07T14:39:29Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:29Z'
---

Reference contexts are privacy-scrubbed and capped before insertion, and a batch is written with one executemany call that retries lock contention and commits once; failures are best-effort.
Why: contexts store raw recall queries that may quote secrets, while reference rows are analytics rather than correctness and must not repeatedly acquire the SQLite write lock.
How to apply: keep every new analytics payload scrubbed and bounded, and extend record_references_batch rather than adding per-row transactions.