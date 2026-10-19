---
type: Tribal-pattern
title: spike fixture probes must stay derivable when the corpus grows
description: spike fixture probes must stay derivable when the corpus grows
resource: scripts/rust_migration/golden_record.py
tags:
- pattern
generated:
  by: cairn/0.22.0
  at: '2026-10-19T11:02:14Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-19T11:02:14Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T11:02:14Z'
---

choose_targets probes centrality hubs for the search_symbols fixture pattern; once the corpus includes minified dashboard assets the probe degenerates to symbols like '$' from which spike_golden.py cannot derive its >=5 queries. Why: hub ranking counts minified-chunk symbols as the hottest nodes. How to apply: when refreshing self goldens over an asset-heavy corpus, pin the search_symbols probe to a meaningful pattern (e.g. Acti*) that yields underscored, capitalized, and long names.