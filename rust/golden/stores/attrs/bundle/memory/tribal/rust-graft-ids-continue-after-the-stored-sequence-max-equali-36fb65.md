---
type: Tribal-decision
title: Rust graft ids continue after the stored sequence max; equality is enforced
  at dump time
description: Rust graft ids continue after the stored sequence max; equality is enforced
  at dump time
resource: rust/crates/cairn-build/src/incremental.rs
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-19T02:54:40Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-19T02:54:40Z'
  action: captured
  score: 0.8
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

IdGen::continuing scans id-keyed tables for sequence-shaped ids (32-hex counters after zero-stripping fit u64; hash/uuid-shaped ids are skipped) and starts the graft's counter above the max, so reinserted rows never collide with rows from the original build. Graft ids therefore differ from a full rebuild's by design, matching the plan's 'id determinism handled at dump time' ruling. How to apply: graft-vs-rebuild equality is asserted on the g2 content-key normalization, never on raw ids; do not try to reproduce rebuild id offsets inside the graft.