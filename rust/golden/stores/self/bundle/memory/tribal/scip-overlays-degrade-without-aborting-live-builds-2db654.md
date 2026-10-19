---
type: Tribal-decision
title: SCIP overlays degrade without aborting live builds
description: SCIP overlays degrade without aborting live builds
resource: src/cairn/graph/builder.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:15Z'
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
- date: '2026-10-07T15:04:15Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:15Z'
---

Missing configured SCIP indexes are generated once, and generation, runtime, or corruption failures become skipped-file records while the tree-sitter graph survives.
Why: an optional precision overlay must not roll back or abort the live graph connection.
How to apply: add new overlay failures as degradation records and never raise through the build boundary.