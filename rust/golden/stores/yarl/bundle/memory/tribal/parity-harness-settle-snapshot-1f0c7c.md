---
type: Tribal-pattern
title: parity harness settle-snapshot
description: parity harness settle-snapshot
tags:
- pattern
generated:
  by: cairn/0.22.0
  at: '2026-10-18T22:53:09Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-18T22:53:09Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T22:53:09Z'
---

The golden parity runner (scripts/rust_migration/parity.py) must settle the store snapshot before record and replay: the python daemon grafts pending file changes into its scratch copy at boot (watcher.ensure_fresh_force) while the rust replay reads the snapshot as-is, so any pending workspace change diverges every store-derived tool (explore, repo_map, trace_flow, ask_compass). Why: record and replay must observe byte-identical store state; the daemon boot catch-up made them differ by construction whenever the workspace had unsynced edits. How to apply: any parity mode that snapshots a live store must run the same catch-up on the copy (parity.py settle_snapshot) before both sides run; re-record goldens only from a quiet workspace.