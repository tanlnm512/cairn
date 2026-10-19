---
type: Tribal-pattern
title: Bench baseline errors fail before suites run
description: Bench baseline errors fail before suites run
resource: src/cairn/cli/bench.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:14:11Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T16:14:11Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:14:11Z'
---

cairn bench --baseline <version> resolves and validates the committed baseline before any suite runs, naming the missing version, the directory searched, and the versions that do exist.
Why: an unknown dataset version otherwise surfaces only after a minutes-long suite, and a usage/baseline error (exit 1) must stay distinct from the regression signal (exit 2).
How to apply: resolve and validate baseline paths up front in new bench entry points and reserve exit 1 for invocation errors.