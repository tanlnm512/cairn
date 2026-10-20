---
type: Tribal-mistake
title: 'python_sum parity: CPython 3.12+ sum() is Neumaier-compensated, naive f64
  fold diverges by 1 ulp'
description: 'python_sum parity: CPython 3.12+ sum() is Neumaier-compensated, naive
  f64 fold diverges by 1 ulp'
tags:
- mistake
generated:
  by: cairn/0.22.0
  at: '2026-10-19T06:12:45Z'
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
- date: '2026-10-19T06:12:45Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T06:12:45Z'
---

Porting Python float sums to Rust needs Neumaier compensated summation, not a plain iter().sum(). `cairn memory stats` avg diverged from Python (0.567 vs 0.568) because CPython 3.12+ built-in sum() uses Neumaier compensation over floats; the naive left fold lost 1 ulp over 24 addends and crossed a round(x,3) boundary. Related: Python round(x, 3) is correct DECIMAL rounding of the exact double (half-to-even) - Rust (x*1000).round()/1000 perturbs before rounding; format!("{x:.3}") then reparse matches Python.
Why: golden-differential CLI tests compare byte output; a 1-ulp sum error surfaces as a different printed average.
How to apply: when porting any Python sum(list_of_floats) whose result is displayed or rounded, reimplement Neumaier summation (python_sum in rust/crates/cairn-cli/src/cmds_knowledge.rs) and round via format-then-reparse, not multiply-round-divide.