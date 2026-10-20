---
type: Tribal-decision
title: Golden fixtures normalize rowid-instability, not just ordering
description: Golden fixtures normalize rowid-instability, not just ordering
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-18T16:40:05Z'
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
- date: '2026-10-18T16:40:05Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T16:40:05Z'
---

The parse pool makes symbol rowids nondeterministic across fresh builds of the same tree, so probes like `ORDER BY id LIMIT 1` flip between captures. Golden targets must be name-ordered, tie-broken, and unique-name constrained; capped FTS lists (ask_compass seeds/callers/callees) flip in MEMBERSHIP, so those lines are masked to member counts, not order-normalized.

Why: two independent golden captures differed in 7/26 tools until probes were stabilized and list lines masked; after the fix, three independent captures verify 26/26 golden-equal.

How to apply: any new golden/parity probe over a cairn-built store must pick targets via deterministic SQL (ORDER BY name, unique-name checks) and treat capped ranked lists as sets, never sequences.