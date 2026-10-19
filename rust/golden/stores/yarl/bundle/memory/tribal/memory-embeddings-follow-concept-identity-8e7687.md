---
type: Tribal-decision
title: Memory embeddings follow concept identity
description: Memory embeddings follow concept identity
resource: src/cairn/graph/embeddings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:44Z'
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
- date: '2026-10-07T14:52:44Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:44Z'
---

Memory concept ids are unstable across tier moves; unchanged memories rename embedding rows in place across all models without committing, new ids embed on demand, and unresolved concepts are periodically reaped.
Why: Why: tier movement changes identity but not content, so re-embedding would waste work and leave avoidable orphan vectors.
How to apply: How to apply: every memory identity move must rename or reap persisted embeddings and leave transaction ownership with the caller.