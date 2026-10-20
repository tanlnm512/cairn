---
type: Tribal-pattern
title: Nested gitignore verdicts cache per repo root
description: Nested gitignore verdicts cache per repo root
resource: src/cairn/graph/scanner.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:12Z'
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
- date: '2026-10-07T15:05:12Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:12Z'
---

Gitignore specifications are compiled per repository and a file verdict is the union of specifications from its directory through the repo root.
Why: nested ignore files have git-relative scopes, and repeated walks dominate scanning without caching.
How to apply: invalidate the repository cache whenever a watched gitignore changes.