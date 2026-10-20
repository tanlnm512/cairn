---
type: Tribal-mistake
title: _neighbor_rows dedupe dropped the caller fuzzy flag
description: _neighbor_rows dedupe dropped the caller fuzzy flag
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-03T15:19:29Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.275
memory_signals:
  graph_verification: 0.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-03T15:19:29Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_refs_baseline: 0.0
valid_from: '2026-10-03T15:19:29Z'
---

The get_callers/get_callees dedupe into tools_graph._neighbor_rows hardcoded False as the first query's fuzzy flag, so an explicit fuzzy=True ran precise-only and returned "No callers found" despite fuzzy-matchable edges. The auto-fallback branch only ran fuzzy when fuzzy=False.
Why: the refactor copied the fallback logic's `False` literal into the primary query; the caller's flag was only consulted for the fallback decision, not the primary lookup.
How to apply: when extracting a shared helper around flag-parameterized queries, pass the caller's flag into the primary query, not just the fallback condition; the tests/test_tools_graph_fallback.py explicit-fuzzy case is the owner test for this contract.