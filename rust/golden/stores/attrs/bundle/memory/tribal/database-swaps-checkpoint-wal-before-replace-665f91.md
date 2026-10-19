---
type: Tribal-decision
title: Database swaps checkpoint WAL before replace
description: Database swaps checkpoint WAL before replace
resource: src/cairn/graph/schema.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:53Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T14:52:53Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:53Z'
---

A database swap checkpoints the old WAL, unlinks old WAL and shared-memory sidecars, then atomically replaces the main file while callers hold the build lock.
Why: Why: surviving old sidecars can replay the pre-swap graph over the new main file or report corruption.
How to apply: How to apply: never reduce a whole-file swap to os.replace alone; checkpoint first and preserve old file descriptors.