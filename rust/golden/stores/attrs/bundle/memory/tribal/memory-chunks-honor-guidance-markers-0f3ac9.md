---
type: Tribal-pattern
title: Memory chunks honor guidance markers
description: Memory chunks honor guidance markers
resource: src/cairn/graph/embeddings.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:45Z'
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
- date: '2026-10-07T14:52:45Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:45Z'
---

Memory chunking prepends the title to the first chunk, splits at Why and How-to-apply markers, falls back to blank paragraphs, and caps the number of chunks.
Why: Why: captured memories already use the guidance structure, giving deterministic boundaries without NLP while bounding embedding cost.
How to apply: How to apply: preserve marker boundaries and the chunk cap when changing memory body capture or embedding.