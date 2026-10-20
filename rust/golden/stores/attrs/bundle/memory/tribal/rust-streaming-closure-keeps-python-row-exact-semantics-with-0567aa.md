---
type: Tribal-decision
title: Rust streaming closure keeps python row-exact semantics with sorted batched
  commits
description: Rust streaming closure keeps python row-exact semantics with sorted batched
  commits
resource: rust/crates/cairn-build/src/closure.rs
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-19T03:04:20Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-19T03:04:20Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

build_transitive_closure (rust/crates/cairn-build/src/closure.rs) reproduces dataflow.py _closure_rows row set exactly: kind-major seeds (sorted call/calls/extends/implements then rowid), insertion-ordered frontier levels with first-wins target assignment, case-1 extension skipping NULL names, case-2 hops in global edge-scan order filtered to has_target edges of globally-unique mid names, and name = COALESCE(symbol.name, target_name) with NULL names as legal frontier keys. Rows stream per level in 50K transaction batches; batches are sorted by (source_id, target_name, distance) before insert so index maintenance appends like python's global sort (content-neutral: row tuples are unique per level). PRAGMA cache_size=-262144 (session-local) keeps index pages hot between commits.

Why: python's level-BFS blew up only through its row-list materialization (single-transaction WAL); a content-different streaming output would fail canonical dump parity.

How to apply: any change to closure row semantics must keep the (source_id, target_name, distance) PK set identical to python's build_transitive_closure; verify with scripts/rust_migration/closure_bench.py which gates on transitive_edges content equality and prints the python/rust time ratio.