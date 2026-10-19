---
type: Tribal-pattern
title: Import-level scip tests need a tree-sitter-only build once the build wires
  the overlay
description: Import-level scip tests need a tree-sitter-only build once the build
  wires the overlay
resource: tests/test_scip_import.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-20T18:43:47Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.543
memory_signals:
  graph_verification: 0.75
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-20T18:43:47Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
stale: true
valid_from: '2026-09-20T18:43:47Z'
memory_refs_baseline: 1.0
memory_stance: preferred
---

Tests that exercise the SCIP importer directly on a built DB (per-file authority DELETE, anomaly-gate skip-row counts) must build their fixture workspace with cairn.json removed, because the wired build now applies the overlay itself: a configured build leaves covered files fully scip-sourced, so an importer-level test's "before" state of tree-sitter rows no longer exists and skip rows accumulate one per import.
Why: T011 wired `_apply_scip_overlay` into builder between `_resolve_all` and `backup_to`; any test asserting pre-overlay tree-sitter state or exact skip-row counts against a configured build double-counts.
How to apply: use the `_build_tree_sitter_only` helper in tests/test_scip_import.py (unlinks cairn.json before build_graph) for importer-level tests; build-level behavior is covered separately in tests/test_builder_scip_overlay.py.