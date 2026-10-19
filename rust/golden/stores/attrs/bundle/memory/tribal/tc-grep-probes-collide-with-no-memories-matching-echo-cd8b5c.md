---
type: Tribal-mistake
title: TC grep probes collide with No-memories-matching echo
description: TC grep probes collide with No-memories-matching echo
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-16T21:00:49Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-16T21:00:49Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-16T21:00:49Z'
---

The temporal-memory TC-001 hidden-leg pattern `! cairn memory search "<probe>" ... | grep -q "<probe>"` also matches the pre-existing stdout echo `No memories matching '<probe>'.`, so the TC fails while behavior is correct; grep the result-row shape (e.g. refs-verified prefix) instead. Why: empty search results still echo the query text on stdout, so any probe string equal to the query self-matches. How to apply: when writing TCs for search/recall commands, anchor greps to the result-row format, not the raw query string.