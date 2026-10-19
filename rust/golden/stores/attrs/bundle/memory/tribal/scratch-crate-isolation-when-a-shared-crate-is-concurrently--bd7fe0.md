---
type: Tribal-pattern
title: Scratch-crate isolation when a shared crate is concurrently edited
description: Scratch-crate isolation when a shared crate is concurrently edited
resource: rust/crates/cairn-parse/src/parser_apple.rs
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-19T00:13:57Z'
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
- date: '2026-10-19T00:13:57Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

When a 2a parser workstream must run cargo tests in cairn-parse while sibling workstreams are mid-edit in the same crate, a sibling's broken file fails the whole lib build. Why: cargo builds the full lib target, so one broken sibling file blocks every workstream's checks. How to apply: copy your module plus its in-crate deps (model, rationale, registry) into a scratch crate in /tmp with the same pinned grammar deps plus a path dep on the vendored dart grammar, add the repo rustfmt.toml, run cargo test and clippy there with RUSTFLAGS=-D warnings to mirror the workspace lint deny, and repoint integration-test fixture paths at the repo; retry the real crate test afterwards since siblings converge within minutes.