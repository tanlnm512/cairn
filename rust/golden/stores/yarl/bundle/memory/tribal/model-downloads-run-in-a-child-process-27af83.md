---
type: Tribal-pattern
title: Model downloads run in a child process
description: Model downloads run in a child process
resource: src/cairn/graph/embeddings.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:39Z'
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
- date: '2026-10-07T14:52:39Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:39Z'
---

Model construction and downloading run in a child interpreter with the parent environment, shared library path, trust-remote-code setting, and HuggingFace cache.
Why: Why: constructing the model in-process emits many progress bars and warnings, while the shared cache makes downloaded weights available to every later process.
How to apply: How to apply: keep runtime trust flags and the cache-visible interpreter path identical when adding download or warmup flows.