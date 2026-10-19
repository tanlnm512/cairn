---
type: Tribal-decision
title: Knowledge index rebuilds are idempotent and never guess pointers
description: Knowledge index rebuilds are idempotent and never guess pointers
resource: src/cairn/knowledge/index.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:08Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:08Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:08Z'
---

Index rebuilds replace edges and doc refs in caller-owned transactions, preserve edge timestamps, report unresolved pointers, and extend a basename pointer only on a unique resource match; declared edges suppress overlap edges and duplicate refs verify with max.
Why: repeated rebuilds must be diff-stable and an ambiguous source pointer must not become a guessed link.
How to apply: keep rebuild and dry-run resolution on shared pointer logic, preserve created_at, and report candidates instead of selecting one.