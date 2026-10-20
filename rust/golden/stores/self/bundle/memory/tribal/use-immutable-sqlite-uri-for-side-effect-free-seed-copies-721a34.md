---
type: Tribal-workaround
title: Use immutable SQLite URI for side-effect-free seed copies
description: Use immutable SQLite URI for side-effect-free seed copies
resource: src/cairn/graph/worktree.py
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-15T18:04:16Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-15T18:04:16Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T18:04:16Z'
---

Open a source graph with immutable=1 before sqlite3.Connection.backup, not mode=ro.\nWhy: mode=ro can still create or touch WAL/SHM sidecars, which violates a byte-for-byte main-store guarantee.\nHow to apply: use immutable=1 only against a quiescent source database, then back up into the independent destination.