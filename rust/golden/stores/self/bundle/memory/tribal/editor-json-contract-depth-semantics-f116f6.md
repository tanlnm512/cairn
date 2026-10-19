---
type: Tribal-decision
title: Editor JSON contract depth semantics
description: Editor JSON contract depth semantics
resource: tests/test_dashboard_editor_contract.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-17T13:47:11Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-09-17T13:47:11Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T13:47:11Z'
---

The /editor/symbol contract takes depth in caller hops, minimum 1 (1 = direct callers); the graph engine's impact_analysis reports direct callers at 0-based depth and covers rows while depth <= max_depth, so the route maps hops to max_depth = hops - 1. blast_radius.depth echoes the effective clamped hops; symbols[].depth stays the engine's 0-based distance. Why: two depth vocabularies in one contract would trap client authors (engine depth=0 returns only direct callers; hops depth=1 does). How to apply: any endpoint remapping impact_analysis must translate and echo the clamp, and counts pin to calls edges only (contains edges never count).