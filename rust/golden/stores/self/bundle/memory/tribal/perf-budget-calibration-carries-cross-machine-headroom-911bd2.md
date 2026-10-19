---
type: Tribal-decision
title: Perf budget calibration carries cross-machine headroom
description: Perf budget calibration carries cross-machine headroom
resource: benchmarks/baselines/perf_p95_budgets.json
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-06T15:44:28Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-06T15:44:28Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-06T15:44:28Z'
---

Initial checked-in p95 budgets are minted at factor 25 from the reference-local DS-v1 run, not factor 10: reference-local is a faster machine class than CI runners, so 10x would false-red the enforcing main/merge-group bench job. Factor 10 is correct only after the first CI-class recalibration; the release checklist documents both states.