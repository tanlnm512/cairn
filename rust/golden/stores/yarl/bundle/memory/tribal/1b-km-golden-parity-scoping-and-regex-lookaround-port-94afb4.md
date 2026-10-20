---
type: Tribal-decision
title: 1b-km golden parity scoping and regex lookaround port
description: 1b-km golden parity scoping and regex lookaround port
tags:
- decision
generated:
  by: cairn/0.22.0
  at: '2026-10-18T19:04:49Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-18T19:04:49Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T19:04:49Z'
---

The 1b-km parity tests make 8/10 km fixtures golden-equal by reconstructing the recorder's own seed_concepts bytes (checked in at rust/golden/km/bundle-seed) on a fresh store; recall_memory and ask_compass fixtures were captured against the live 490-file memory bundle (now drifted to 493) plus the full 657-file graph, which are not recoverable, so those two are covered by seeded contract tests instead.

Why: recall scores are frozen in frontmatter (reproducible), but the corpus itself and the graph-backed ask_compass fallback are not checked in; recreating them would fake parity.

How to apply: when the golden corpus is regenerated (scripts/rust_migration/golden_record.py), re-evaluate whether recall_memory/ask_compass can graduate to golden-equal tests. Also: the Rust regex crate has no lookaround - Python's bookkeeping patterns were ported as per-line date+count conjunction and a leading-boundary branch alternative.