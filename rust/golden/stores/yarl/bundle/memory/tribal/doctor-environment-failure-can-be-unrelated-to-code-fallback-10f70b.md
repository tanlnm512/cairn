---
type: Tribal-mistake
title: Doctor environment failure can be unrelated to code fallback health
description: Doctor environment failure can be unrelated to code fallback health
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-15T17:44:22Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-15T17:44:22Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T17:44:22Z'
---

cairn doctor can exit nonzero when registered MCP client endpoints have no running shared daemon even while schema, embeddings, ANN, freshness, parse, concurrency, memory, and config checks pass.\nWhy: environment registration is an external runtime dependency, unlike code-path degradation.\nHow to apply: distinguish the failing environment check from code fallback results before attributing a nonzero doctor exit to the change.