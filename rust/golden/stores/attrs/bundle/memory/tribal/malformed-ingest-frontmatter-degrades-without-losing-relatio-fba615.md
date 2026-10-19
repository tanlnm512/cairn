---
type: Tribal-pattern
title: Malformed ingest frontmatter degrades without losing relationships
description: Malformed ingest frontmatter degrades without losing relationships
resource: src/cairn/knowledge/ingest/parser.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:10Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:10Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:10Z'
---

Source parsing tries YAML first, then a minimal clean-pair fallback; unknown keys stay verbatim, flow strings recover structure, and relationship entries normalize and dedupe idempotently.
Why: malformed frontmatter should not abort otherwise useful documents, while downstream storage needs typed relationship records.
How to apply: keep the recovery fallback local and normalize every relationship entry before staging or storage.