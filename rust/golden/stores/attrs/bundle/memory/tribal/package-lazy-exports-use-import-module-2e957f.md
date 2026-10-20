---
type: Tribal-pattern
title: Package lazy exports use import_module
description: Package lazy exports use import_module
resource: src/cairn/graph/__init__.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:17Z'
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
- date: '2026-10-07T15:18:17Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:17Z'
---

Lazy submodule exports use importlib.import_module instead of from . import module. Why: the relative-import form looks up the package attribute and can re-enter module __getattr__ before the submodule is cached. How to apply: resolve lazy package exports directly through import_module, then cache the result in globals.