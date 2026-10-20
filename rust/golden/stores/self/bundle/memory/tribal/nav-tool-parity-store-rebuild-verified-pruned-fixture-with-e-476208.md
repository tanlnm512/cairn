---
type: Tribal-decision
title: 'Nav-tool parity store: rebuild-verified pruned fixture with edges'
description: 'Nav-tool parity store: rebuild-verified pruned fixture with edges'
resource: scripts/rust_migration/nav_golden.py
tags:
- decision
generated:
  by: cairn/0.22.0
  at: '2026-10-18T19:01:11Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-18T19:01:11Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T19:01:11Z'
---

Phase-1 nav parity uses a second pruned store fixture, rust/golden/nav/nav-store.kg, keeping repos/files/symbols/edges/imports (no FTS/embeddings/closure) because the P0-5 spike store dropped edges and cannot serve callers/callees/impact.
Why: goldens were captured from a scratch store deleted after capture, so the only faithful store is a rebuild verified response-equal; scripts/rust_migration/nav_golden.py rebuilds the self-store, proves all four nav goldens still reproduce (tree drift since the original capture lives outside the queried neighborhoods), then prunes and re-verifies slim-vs-full.
How to apply: regenerate with .venv/bin/python scripts/rust_migration/nav_golden.py after corpus-affecting changes; the Rust test crates/cairn-query/tests/nav_golden.rs reads only the checked-in bytes; the golden impact query hits the DFS (seed cycle present), so dropping the closure tables keeps responses equal.