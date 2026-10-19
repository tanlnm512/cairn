---
type: Tribal-mistake
title: Recursive package-data globs ship bytecode caches
description: Recursive package-data globs ship bytecode caches
resource: pyproject.toml
tags:
- mistake
generated:
  by: cairn/0.21.1
  at: '2026-09-22T18:05:16Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.634
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.96
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-22T18:05:16Z'
  action: captured
  score: 0.96
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-22T18:05:16Z'
---

Setuptools package-data patterns must enumerate asset and script extensions instead of using an unrestricted recursive wildcard.\nWhy: **/* matches generated __pycache__/*.pyc files, so local build state leaks into sdists and wheels.\nHow to apply: For template trees, list the shipped file types and add a packaging test that asserts every intended asset matches while bytecode caches do not.