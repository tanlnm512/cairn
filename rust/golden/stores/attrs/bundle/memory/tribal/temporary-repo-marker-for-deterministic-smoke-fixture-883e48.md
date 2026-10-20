---
type: Tribal-pattern
title: Temporary repo marker for deterministic smoke fixture
description: Temporary repo marker for deterministic smoke fixture
resource: .github/workflows/weekly-smoke.yml
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-05T17:33:58Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-05T17:33:58Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-05T17:33:58Z'
---

When replaying against the vendored repository snapshot, copy it to a temporary workspace and create an empty .git marker on the copy before building; never add the marker to the vendored tree.