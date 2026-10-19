---
type: Tribal-pattern
title: Ingest directory skips match nested path prefixes
description: Ingest directory skips match nested path prefixes
resource: src/cairn/knowledge/ingest/adapters.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:19Z'
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
memory_tier: archived
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:19Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-10-07T17:31:19Z'
---

Directory skip rules match individual path segments and joined parent-path prefixes, covering both one-segment defaults and multi-segment workspace patterns.
Why: a docs/notes workspace rule would otherwise never match any single path segment.
How to apply: evaluate both forms before accepting a repository document.