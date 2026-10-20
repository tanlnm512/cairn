---
type: Tribal-pattern
title: Baseline p95 latency is dominated by the per-call freshness walk, not queries
description: Baseline p95 latency is dominated by the per-call freshness walk, not
  queries
resource: src/cairn/graph/watcher.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-18T15:58:12Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.6
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.8
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-18T15:58:12Z'
  action: captured
  score: 0.8
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T15:58:12Z'
---

The P0-2 Python baselines (specs/rust-migration/baselines.md, harness scripts/rust_migration/baseline.py) drive all 26 MCP tools over live streamable HTTP against a freshly built scratch store. On the cairn self store every graph-layer tool carries a ~300-400 ms floor because refresh_for_query -> _detect_changed (src/cairn/graph/watcher.py) re-stats all indexed files and re-walks the repo source tree before EACH graph read; knowledge-layer tools that skip the guard run at 1-14 ms on the same store.
Why: the freshness guard is per-call, not per-session, and its cost scales with indexed-file count and tree-walk size, not with the query itself.
How to apply: Phase 1 (Rust daemon) must decide explicitly whether the per-call freshness walk is a frozen tool contract or an implementation detail — golden fixtures will match either way only if the decision is recorded; do not read the ~300 ms as SQLite/query cost when comparing Rust read-path latency.