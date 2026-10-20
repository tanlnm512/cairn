---
type: Tribal-mistake
title: Pack cost tests must pin tokenizer mode
description: Pack cost tests must pin tokenizer mode
resource: tests/test_pack.py
tags:
- mistake
generated:
  by: cairn/0.21.1
  at: '2026-09-25T16:33:06Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.63
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.94
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-25T16:33:06Z'
  action: captured
  score: 0.94
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-25T16:33:06Z'
---

Tests that construct PackItem directly must pin the process-global tokenizer singleton to heuristic mode before calculating expected budgets.\nWhy: Exact tokenizer availability or a leaked exact-mode fixture changes PackItem costs; the test then fails only on semantic-extra machines or under particular xdist orderings.\nHow to apply: Call the pack suite's heuristic-tokenizer pin fixture/helper before PackItem.create in every direct cost assertion.