---
type: Tribal-decision
title: Read-only diagnostics never materialize a missing store
description: Read-only diagnostics never materialize a missing store
resource: src/cairn/cli/system/doctor.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:29:05Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T16:29:05Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:29:05Z'
---

doctor and report treat a non-existent --db path as an unavailable store (schema FAIL, remaining DB-dependent checks WARN/degrade) instead of letting SQLite create it.
Why: creating a fresh store would mask a mistyped --db with an all-PASS fresh-install snapshot, and both commands are read-only diagnostics.
How to apply: every new diagnostic surface must check path existence before opening and degrade, never create.