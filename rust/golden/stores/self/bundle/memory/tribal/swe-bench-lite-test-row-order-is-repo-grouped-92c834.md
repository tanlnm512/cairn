---
type: Tribal-pattern
title: SWE-bench_Lite test row order is repo-grouped
description: SWE-bench_Lite test row order is repo-grouped
resource: benchmarks/datasource/swe-bench-subset.json
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T10:26:14Z'
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
- date: '2026-09-17T10:26:14Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T10:26:14Z'
---

At princeton-nlp/SWE-bench_Lite revision 6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2, the test split's parquet row order is grouped by repo, so a positional first-N slice is not repo-diverse: the frozen 50-instance subset (benchmarks/datasource/swe-bench-subset.json) spans only astropy and django. Any future subset version wanting repo spread must use a selection beyond the positional-slice idiom (D-002 documents the frozen-list-not-edit rule).