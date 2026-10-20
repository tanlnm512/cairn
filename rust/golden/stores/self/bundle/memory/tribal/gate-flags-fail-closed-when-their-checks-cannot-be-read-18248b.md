---
type: Tribal-pattern
title: Gate flags fail closed when their checks cannot be read
description: Gate flags fail closed when their checks cannot be read
resource: src/cairn/cli/review.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-15T15:37:00Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-15T15:37:00Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-15T15:37:00Z'
---

review --gate exits 1 when the memory reader errored, mirroring the embed-deferral rule that derived-data failures never invalidate the primary write. Why: has_blocking_matches returns False on reader error, which would pass an unverified diff. How to apply: any --*-gate flag checks its section's error field before trusting an empty match set.