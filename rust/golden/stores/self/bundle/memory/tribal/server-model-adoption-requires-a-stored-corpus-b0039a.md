---
type: Tribal-decision
title: Server-model adoption requires a stored corpus
description: Server-model adoption requires a stored corpus
resource: src/cairn/cli/embed.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:20:18Z'
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
- date: '2026-10-07T16:20:18Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:20:18Z'
---

--adopt-server-model checks that the embeddings table has rows before evaluating the ladder.
Why: an alias binding needs a stored corpus to latch onto — with no rows there is nothing to verify parity against, and the empty table deserves its own remediation rather than the ladder's refusal.
How to apply: gate any new adoption path on a non-empty corpus and give the empty-store case its own guidance.