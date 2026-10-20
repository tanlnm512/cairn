---
type: Tribal-decision
title: Exact symbol candidates expose ambiguity instead of a silent first row
description: Exact symbol candidates expose ambiguity instead of a silent first row
resource: src/cairn/dashboard/data.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:31Z'
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
- date: '2026-10-07T16:57:31Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:31Z'
---

Exact-name lookup returns every same-name symbol with file and repo context rather than adopting the visualization layer’s LIMIT 1 row.
Why: callers need to disambiguate definitions and dangling file rows must still render instead of disappearing.
How to apply: use symbol_candidates for exact-name selection and preserve its deterministic file, repo, id order and truncated flag.