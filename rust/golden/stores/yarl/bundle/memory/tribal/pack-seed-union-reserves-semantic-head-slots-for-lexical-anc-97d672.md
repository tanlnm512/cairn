---
type: Tribal-pattern
title: pack seed union reserves semantic head slots for lexical anchors
description: pack seed union reserves semantic head slots for lexical anchors
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T08:27:27Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T08:27:27Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T08:27:27Z'
---

cairn pack seed_symbols unions semantic hits with lexical term-mode anchors (search_symbols_terms on task.split()). A SEED_LIMIT-capped union is a no-op when the semantic leg fills the cap (bench-proven: semantic returns exactly 10 hits), so the semantic leg admits at most SEMANTIC_SEED_LIMIT (5) head slots and lexical-only additions fill the tail to SEED_LIMIT. Union order semantic-first keeps ranking primacy; head list is truncated locally because stubs/surfaces may not honor their limit arg.