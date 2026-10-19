---
type: Tribal-mistake
title: parity record drops spike_queries from spike-family fixtures
description: parity record drops spike_queries from spike-family fixtures
resource: scripts/rust_migration/golden_record.py
tags:
- mistake
generated:
  by: cairn/0.22.0
  at: '2026-10-19T11:02:03Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-19T11:02:03Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T11:02:03Z'
---

write_fixtures rewrites search_symbols.json and semantic_search.json wholesale, so every parity.py/golden_record.py self-golden refresh silently removes the spike_queries key the cargo spike_golden and golden_search tests replay. Why: the recorder's fixture shape has no spike_queries field and the spike harnesses were not re-run after the last record (cfce44a). How to apply: after any self-golden record, re-run scripts/rust_migration/spike_golden.py and scripts/rust_migration/semantic_golden.py, then scripts/rust_migration/flow_golden.py for the four flow-family fixtures it owns.