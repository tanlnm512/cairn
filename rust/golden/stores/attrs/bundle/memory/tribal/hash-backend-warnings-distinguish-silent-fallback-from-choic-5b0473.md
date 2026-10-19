---
type: Tribal-decision
title: Hash-backend warnings distinguish silent fallback from choice
description: Hash-backend warnings distinguish silent fallback from choice
resource: src/cairn/cli/embed.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:20:19Z'
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
- date: '2026-10-07T16:20:19Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:20:19Z'
---

Hash-backend warnings fire only when the configured local backend silently degraded to hash because sentence-transformers is missing; an explicitly configured hash backend is an informed choice and never warns.
Why: a warning that fires on a deliberate configuration teaches users to ignore warnings, while the silent default fallback is a real retrieval degradation (token-overlap vectors instead of semantics).
How to apply: key any new backend warning on configured-intent versus effective-state divergence, mirroring embeddings.is_hash_fallback().