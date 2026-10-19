---
type: Tribal-pattern
title: PHP parser edges use bare namespace tails
description: PHP parser edges use bare namespace tails
resource: src/cairn/parsers/php.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:15:10Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.628
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.93
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:15:10Z'
  action: captured
  score: 0.93
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:15:10Z'
---

PHP call and receiver names reduce qualified names such as App\Models\User to User before building edges or inferred receiver types.

Why: graph symbols and the import-aware resolver split / and . paths but not PHP backslash namespaces, so full qualified names cannot match bare stored symbol names.
How to apply: preserve qualified_name for navigation, but keep call targets and receiver types as the final backslash segment.