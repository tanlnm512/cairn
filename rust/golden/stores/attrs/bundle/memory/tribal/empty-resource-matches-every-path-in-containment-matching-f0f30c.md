---
type: Tribal-pattern
title: Empty resource matches every path in containment matching
description: Empty resource matches every path in containment matching
resource: src/cairn/pack_enrich.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T06:09:33Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T06:09:33Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T06:09:33Z'
---

Substring-containment matching (needle in haystack) with an empty needle is always true; guard the needle for truthiness before matching. Why: OKF compass concepts may carry no resource field, and an empty resource would make every file path match the first resourceless concept. How to apply: in any resource-in-path / path-in-resource matcher, test the resource value truthy before the containment test.