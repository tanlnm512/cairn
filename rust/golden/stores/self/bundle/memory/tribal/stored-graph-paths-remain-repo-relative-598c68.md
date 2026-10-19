---
type: Tribal-decision
title: Stored graph paths remain repo-relative
description: Stored graph paths remain repo-relative
resource: src/cairn/graph/scanner.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:11Z'
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
- date: '2026-10-07T15:05:11Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:11Z'
---

Graph rows store repo-relative paths, and disk access resolves them through one workspace-aware chokepoint that returns absolute paths unchanged.
Why: the store stays portable across machines and every consumer honors the same repository root.
How to apply: never open a stored relative path directly; resolve it through the shared scanner helper.