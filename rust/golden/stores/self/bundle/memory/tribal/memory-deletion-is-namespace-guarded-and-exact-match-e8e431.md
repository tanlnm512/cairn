---
type: Tribal-decision
title: Memory deletion is namespace-guarded and exact-match
description: Memory deletion is namespace-guarded and exact-match
resource: src/cairn/memory/store.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:35Z'
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
- date: '2026-10-07T14:39:35Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:35Z'
---

Deletion resolves memory paths with symlink-aware namespace checks, converts escape errors into a false refusal, removes references by exact absolute and relative concept-id matches, and does not commit.
Why: fallback resolution can otherwise unlink compass, wiki, or knowledge docs, substring cleanup can delete unrelated memories, and committing an injected connection can break a caller transaction.
How to apply: keep memory/ enforcement at the store chokepoint, resolve both filesystem sides before comparing, and update every persisted id form without committing.