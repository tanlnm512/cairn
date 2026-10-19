---
type: Tribal-decision
title: Build failures keep the store-exists exit contract
description: Build failures keep the store-exists exit contract
resource: src/cairn/cli/core.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:16:08Z'
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
- date: '2026-10-07T16:16:08Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:16:08Z'
---

When a build's persist phase produces no store and the result cannot be opened, the command fails with 'build produced no store' instead of reporting a skipped dataflow phase with exit 0.
Why: the exit code is the store-exists contract scripting keys on; a silent skip makes the missing store invisible to automation.
How to apply: treat an unopenable post-build store as a command failure, never as a degraded-success note.