---
type: Tribal-decision
title: Bench profile comparisons bucket hosted-runner identity
description: Bench profile comparisons bucket hosted-runner identity
resource: src/cairn/cli/bench.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:14:11Z'
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
- date: '2026-10-07T16:14:11Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:14:11Z'
---

Machine profiles compare at class level for the rolling CI baseline: runner_class strips GitHub's per-instance trailing digits, two Linux kernels compare as one hosted class while macOS point releases stay exact, every other field (arch, cpu, cpu_count) stays exact-equality, and benchmark timings themselves are never normalized.
Why: the baseline is minted on main and compared PR-over-PR on the same hosted pool, so instance-level identity is not a comparability fact — warning on it would flag every CI comparison, while arch/CPU differences are real.
How to apply: keep the mismatch warning advisory so it never changes the exit code, and classify each new profile field as instance identity or comparability signal before comparing it.