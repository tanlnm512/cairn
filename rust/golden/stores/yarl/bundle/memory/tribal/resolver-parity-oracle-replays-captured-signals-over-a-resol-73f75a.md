---
type: Tribal-decision
title: Resolver parity oracle replays captured signals over a resolution-pinned python
  build
description: Resolver parity oracle replays captured signals over a resolution-pinned
  python build
resource: scripts/rust_migration/resolver_parity.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-19T01:12:53Z'
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
- date: '2026-10-19T01:12:53Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

Workstream 2b's differential check (scripts/rust_migration/resolver_parity.py) hooks cairn.graph.resolver.resolve_repo_edges during a fresh python build to capture the exact per-edge in-memory signals (receiver_type/call_arity/generic_tier are parser signals, never stored in the edges table), pins the build to pure resolver output (lsp=False, materialize_import_edges and _apply_scip_overlay no-oped), then replays the signals through cairn-build's resolve_parity example on a store copy and compares per-repo label counts plus byte-identical kg_dump.py --tables edges dumps.

Why: replaying resolution from a built store alone cannot reproduce python's labels because the resolver's tier-0/tie-break inputs exist only in memory at build time, and LSP/SCIP/import-materialization mutate edges after resolution, masking real resolver diffs.

How to apply: reuse this capture/replay shape for the 2c builder and 2d incremental differentials; any new post-resolution edges-table mutator must be added to the harness's pin list or the oracle goes false-negative.