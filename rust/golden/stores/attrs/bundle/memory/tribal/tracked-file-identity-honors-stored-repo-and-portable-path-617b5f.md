---
type: Tribal-pattern
title: Tracked file identity honors stored repo and portable path
description: Tracked file identity honors stored repo and portable path
resource: src/cairn/graph/incremental.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:41Z'
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
- date: '2026-10-07T15:04:41Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:41Z'
---

Path lookup prefers the stored repo-relative row with an absolute fallback, and downstream writes use the stored repo id.
Why: files.path is portable and inferred repositories may differ from the persisted foreign-key owner.
How to apply: resolve through the shared tracked-row helper and let insertion create any missing repo row.