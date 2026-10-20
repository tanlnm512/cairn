---
type: Tribal-pattern
title: 'Taint surface tests: blast needs a committed git fixture copy'
description: 'Taint surface tests: blast needs a committed git fixture copy'
resource: tests/test_taint_surfaces.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T02:56:40Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T02:56:40Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T02:56:40Z'
---

Fixture copies for cairn taint tests only need an empty .git dir, but cairn blast tests must git-init and commit the copy: compute_blast seeds changed symbols from the working-tree diff against HEAD. Edit a line inside the target function's span so the unified=0 hunk seeds exactly that symbol.