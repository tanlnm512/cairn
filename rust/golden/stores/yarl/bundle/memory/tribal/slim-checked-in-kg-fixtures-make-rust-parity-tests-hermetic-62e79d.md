---
type: Tribal-decision
title: Slim checked-in .kg fixtures make Rust parity tests hermetic
description: Slim checked-in .kg fixtures make Rust parity tests hermetic
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-18T17:26:57Z'
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
- date: '2026-10-18T17:26:57Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T17:26:57Z'
---

P0-5's rmcp spike test needs a Python-built `.kg` whose responses are golden-equal, but the live self-store is ~174 MB and CI has no Python. Approach that worked: `scripts/rust_migration/spike_golden.py` builds the self-store corpus (build_graph + dataflow, embed skipped), captures 6 query responses through the real tool functions, prunes a backup copy to the tables `search_symbols` reads (repos/files/symbols/symbols_fts*), VACUUMs to DELETE journal mode (15.1 MB, no -wal/-shm on read-only open), verifies the slim copy response-equal for every query, then appends `spike_queries` to rust/golden/mcp/search_symbols.json.
Why: bm25 scores and their order depend only on the FTS-indexed columns, so dropping vec/dataflow/edges/knowledge tables preserves search parity bit-for-bit (verified), and the frozen checked-in bytes make the Rust integration test hermetic and CI-runnable.
How to apply: Phase 1 parity tests can reuse this pattern — check in slim, verified store fixtures per query surface instead of rebuilding Python stores in cargo test; regenerate only when the corpus contract changes, and re-verify slim-vs-full equality in the regen script. The Rust spike reads it read-only via rusqlite bundled + sqlite3_auto_extension(sqlite3_vec_init) (D-003) — vec0 registration is a no-op on the slim store but exercises the real store stack.