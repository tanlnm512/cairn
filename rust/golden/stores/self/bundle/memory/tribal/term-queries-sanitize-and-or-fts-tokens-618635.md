---
type: Tribal-decision
title: Term queries sanitize and OR FTS tokens
description: Term queries sanitize and OR FTS tokens
resource: src/cairn/graph/lexical.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:38Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.617
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.88
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:18:38Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:38Z'
---

Enriched terms become independent quoted prefix tokens, deduplicated case-insensitively and OR-combined. Why: folding a sentence into one phrase creates empty BM25 results, while user terms can carry FTS metacharacters. How to apply: reduce terms to alphanumeric tokens before quoting and keep prefix stars outside quotes.