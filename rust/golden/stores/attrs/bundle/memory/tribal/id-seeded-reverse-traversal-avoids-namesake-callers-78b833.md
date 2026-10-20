---
type: Tribal-pattern
title: ID-seeded reverse traversal avoids namesake callers
description: ID-seeded reverse traversal avoids namesake callers
resource: src/cairn/graph/traversal.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-15T18:49:26Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-15T18:49:26Z'
  action: captured
  score: 0.94
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T18:49:26Z'
---

Reverse graph walks seeded from an edited span should pass the stored symbol id through traversal; name-only roots treat every same-named definition as a seed.\n\nWhy: exact edges carry target ids, so an id root keeps unrelated namesake dependents out of the radius.\n\nHow to apply: use the id-aware traversal seam when the caller already possesses symbol ids; retain name lookup only for user-supplied names.