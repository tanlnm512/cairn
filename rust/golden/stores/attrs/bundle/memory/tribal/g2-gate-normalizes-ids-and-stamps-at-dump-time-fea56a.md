---
type: Tribal-decision
title: G2 gate normalizes ids and stamps at dump time
description: G2 gate normalizes ids and stamps at dump time
resource: scripts/rust_migration/g2_check.sh
tags:
- decision
generated:
  by: cairn/0.22.0
  at: '2026-10-19T02:00:43Z'
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
- date: '2026-10-19T02:00:43Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T02:00:43Z'
---

The G2 gate (scripts/rust_migration/g2_check.sh) runs kg_dump.py on both stores, then rewrites uuid/counter row ids to content-derived keys and blanks volatile stamps (indexed_at/recorded_at/timestamp; parse_errors.stack_trace) symmetrically on BOTH dumps before kg_diff.py; build_runs/events/tool_metrics/dataflow sections are dropped (run telemetry plus a CLI-derived index outside the Phase 2 build path).

Why: builder.py generates uuid4 ids and wall-clock stamps per run, so even two python builds of one corpus are never raw-dump-equal (measured: 7 diff groups python-vs-python). plan.md 2c id determinism handled at dump time is this normalization; the rust builder is free to use counter ids.

How to apply: when a G2 dump diff shows id-shaped-only differences, extend the normalizer content-key rules rather than changing id generation on either side.