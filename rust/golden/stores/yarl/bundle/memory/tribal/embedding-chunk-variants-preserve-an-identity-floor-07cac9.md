---
type: Tribal-decision
title: Embedding chunk variants preserve an identity floor
description: Embedding chunk variants preserve an identity floor
resource: src/cairn/graph/embeddings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:35Z'
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
- date: '2026-10-07T14:52:35Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:35Z'
---

Chunk variants A, B, C, and the B field-dropout recipes all retain qualified name, file path, signature, and docstring; explicit variant selection overrides the environment case-insensitively without mutating it.
Why: Why: the ablation ladder must compare field contributions without losing the minimum fields needed to identify a symbol.
How to apply: How to apply: extend CHUNK_VARIANTS only with a recipe that preserves the identity floor and keep explicit selection environment-neutral.