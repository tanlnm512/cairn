---
type: Tribal-pattern
title: Seed the CI knowledge bundle or pre-submit findings never fire
description: Seed the CI knowledge bundle or pre-submit findings never fire
resource: paths
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T05:04:40Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T05:04:40Z'
  action: captured
  score: 0.8
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T05:04:40Z'
---

Pre-submit review reads mistake and pattern memories from the OKF bundle at the knowledge path resolved by `resolve_store` in `src/cairn/paths.py`; a fresh CI store is memory-empty, so keyed warnings never fire. How to apply: point CAIRN_KNOWLEDGE at the committed seed bundle `.cairn-knowledge` before `build_pre_submit` runs, or record memories into the CI store first.