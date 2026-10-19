---
type: Tribal-decision
title: Filename markers skip vendored minified bundles
description: Filename markers skip vendored minified bundles
resource: src/cairn/graph/scanner.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:13Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:05:13Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:13Z'
---

Minified filename markers skip bundles independently of directory filters and before the size cap.
Why: a committed minified file under source produces hundreds of generated symbols that dominate graph views even below the cap.
How to apply: treat marker additions as a scanning contract change covered by skip-reporting tests.