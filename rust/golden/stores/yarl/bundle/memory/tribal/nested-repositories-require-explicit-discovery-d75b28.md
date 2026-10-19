---
type: Tribal-decision
title: Nested repositories require explicit discovery
description: Nested repositories require explicit discovery
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

Workspace discovery returns configured child repositories and falls back to the workspace root when only it is a repository.
Why: accidentally indexing nested dependencies duplicates vendor code, while the common single-repo layout still needs a repo identity.
How to apply: keep nested repositories opt-in and preserve the root fallback for root-initialized workspaces.