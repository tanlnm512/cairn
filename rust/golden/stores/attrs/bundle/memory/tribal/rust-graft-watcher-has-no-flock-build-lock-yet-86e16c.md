---
type: Tribal-decision
title: Rust graft/watcher has no flock build lock yet
description: Rust graft/watcher has no flock build lock yet
resource: rust/crates/cairn-build/src/watcher.rs
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-19T02:54:59Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.589
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.75
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-19T02:54:59Z'
  action: captured
  score: 0.75
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

The Python watcher serializes update passes through schema.build_lock (flock on <db>.build.lock) and absorbs lock contention into pending_sync retries. The Rust port serializes only its own passes (busy flag in the debounce worker); no cross-process lock exists. Why: no concurrent Rust writer exists yet (no daemon wiring, no CLI update command); adding flock is trivial once a second writer lands. How to apply: when the Rust daemon or CLI update command first calls incremental_update concurrently with another writer, port build_lock first or update passes can interleave.