---
type: Tribal-decision
title: check verb records intent rows at the CLI layer
description: check verb records intent rows at the CLI layer
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-17T00:56:26Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-09-17T00:56:26Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-09-17T00:56:26Z'
---

The cairn memory check verb records kind='intent' rows via a CLI-level refresh-or-insert (UPDATE ts, else INSERT) on agent_symbols, because the store surface exposes only share_memory (kind pinned to 'share') and check_overlap (pure read). Re-checking a symbol refreshes the intent ts so repeat checks stay inside the 7-day recency window; one intent row per (agent, symbol) keeps the table bounded.
Why: T010 owns only the CLI file; store.py is a wave-mate's file.
How to apply: an intent writer at the store layer would absorb this upsert; until then the CLI SQL is the only intent-write path.