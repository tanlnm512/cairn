---
type: Tribal-pattern
title: Git diff parsing tolerates late binary bytes
description: Git diff parsing tolerates late binary bytes
resource: src/cairn/graph/blast.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:23Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.617
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.88
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:18:23Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:23Z'
---

Git output is decoded with replacement and paths use core.quotePath=false. Why: git examines only a prefix for binary detection while path parsing needs byte-compatible non-ASCII paths. How to apply: keep diff parsing restricted to hunk and path syntax and preserve files.path-compatible path bytes.