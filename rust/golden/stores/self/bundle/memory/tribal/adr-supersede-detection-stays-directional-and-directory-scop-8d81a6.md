---
type: Tribal-decision
title: ADR supersede detection stays directional and directory-scoped
description: ADR supersede detection stays directional and directory-scoped
resource: src/cairn/knowledge/ingest/adr.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:20Z'
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
- date: '2026-10-07T17:31:20Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:20Z'
---

Supersede markers distinguish old-to-new direction, tolerate common spellings, exclude fenced code, and resolve numbers only within the same repository and directory; the first sorted document wins and generated ids reuse staging's doc-type slug.
Why: marker direction determines both relation edges, while ADR numbers alone can collide across directories and repositories.
How to apply: carry marker direction into canonical newer/older pairs and resolve through the scoped number index.