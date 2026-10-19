---
type: Tribal-decision
title: Adopted server models pin config, not restamps
description: Adopted server models pin config, not restamps
resource: src/cairn/cli/embed.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:20:19Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
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
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:20:19Z'
---

Adopting a server model persists the alias binding in ~/.cairn/config.json as the durable home; the printed env export remains only for env-only setups, and the corpus keeps its stored stamp (no re-embed, no restamp).
Why: an env var dies with the shell while the binding must survive sessions, and restamping the corpus would discard every stored vector.
How to apply: write new persistent embedding pins to the config layer and keep stamps read from the corpus.