---
type: Tribal-pattern
title: Ingest directory skips match nested repo path prefixes
description: Ingest directory skips match nested repo path prefixes
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T18:23:18Z'
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
- date: '2026-10-07T18:23:18Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T18:23:18Z'
---

Ingest skip directories are matched as nested path prefixes of the source path, not as bare names.
Why: a bare-name match would skip same-named directories anywhere in a corpus; prefix matching scopes the skip to the configured location.
How to apply: when adding or testing ingest skip rules, construct cases where a same-named directory exists outside the configured prefix and assert it is still ingested.