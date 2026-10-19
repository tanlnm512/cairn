---
type: Tribal-decision
title: Test detection patterns stay conservative
description: Test detection patterns stay conservative
resource: src/cairn/graph/tests.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:19:04Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:19:04Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:19:04Z'
---

Test path patterns must represent real test conventions rather than words that occur in production paths. Why: broad matching pollutes impact filtering with production symbols. How to apply: add only unambiguous path conventions and classify a file when any pattern matches.