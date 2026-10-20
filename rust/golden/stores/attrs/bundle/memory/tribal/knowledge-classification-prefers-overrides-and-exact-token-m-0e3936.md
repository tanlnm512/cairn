---
type: Tribal-decision
title: Knowledge classification prefers overrides and exact token matches
description: Knowledge classification prefers overrides and exact token matches
resource: src/cairn/knowledge/ingest/classifier.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:18Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:18Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:18Z'
---

Workspace title rules run first, then built-in title rules, path conventions, and the spec default; decision precedes reference, keywords match whole tokens, and phrases match normalized text.
Why: workspace refinements must win, an ADR title is a decision rather than a reference, and substrings such as arch inside search must not classify.
How to apply: preserve rule order and use token/normalized-phrase matching for built-in and workspace rules.