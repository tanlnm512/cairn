---
type: Tribal-mistake
title: doctor environment failure can be unrelated to a code change
description: doctor environment failure can be unrelated to a code change
resource: src/cairn/graph/lsp.py
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-15T18:15:38Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.6
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.8
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-15T18:15:38Z'
  action: captured
  score: 0.8
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T18:15:38Z'
---

Doctor can report FAIL because registered SSE clients have no running shared daemon even when schema, embeddings, freshness, and parsing pass.\nWhy: Environment checks cover external daemon registration, not only the changed code path.\nHow to apply: Separate daemon registration failures from code regressions before changing implementation code; start or re-register the daemon only as an environment operation.