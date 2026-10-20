---
type: Tribal-pattern
title: Time closure/dataflow builds through schema.get_db, not sqlite3.connect
description: Time closure/dataflow builds through schema.get_db, not sqlite3.connect
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-20T17:15:17Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-20T17:15:17Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-20T17:15:17Z'
---

Benchmarks and tests that time cairn's SQL-heavy derived-index builds (e.g. build_transitive_closure) must open the DB through cairn.graph.schema.get_db, never a bare sqlite3.connect.
Why: measured this session — the identical closure build over the identical 1000-file corpus DB took ~399 s via sqlite3.connect vs ~6 s via get_db; get_db sets WAL + mmap_size + busy_timeout PRAGMAs (schema.py get_db), and the per-level INSERT OR IGNORE churn is ~60x slower on a default-journal connection. A probe on a bare connection mis-measures so badly it looks like a redesign failed when it passed.
How to apply: any timing gate, probe, or ad-hoc measurement of builder/dataflow code should use get_db(db_path); a plain connect is only for read-only assertions.