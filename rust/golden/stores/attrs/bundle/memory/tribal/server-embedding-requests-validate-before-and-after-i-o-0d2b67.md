---
type: Tribal-decision
title: Server embedding requests validate before and after I/O
description: Server embedding requests validate before and after I/O
resource: src/cairn/graph/embeddings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:41Z'
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
- date: '2026-10-07T14:52:41Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:41Z'
---

Batch size and timeout are validated as positive finite values before requests; transient network, timeout, 429, and 5xx failures retry while other client errors surface the server message; every 200 envelope, ordering, count, and dimension is checked.
Why: Why: malformed configuration or responses must fail as named, non-retryable errors rather than zero-vector success or mid-write KeyErrors.
How to apply: How to apply: add new server knobs to preflight validation and extend envelope checks before consuming response data.