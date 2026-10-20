---
type: Tribal-decision
title: Registration probes fail only on provably different stores
description: Registration probes fail only on provably different stores
resource: src/cairn/cli/system/doctor.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:29:04Z'
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
- date: '2026-10-07T16:29:04Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:29:04Z'
---

A doctor registration spawn probe records FAIL only when it provably resolves a different EXISTING store, naming both stores; a probe that errors, times out, or resolves a store missing on disk stays a WARN, and an unreachable SSE endpoint FAILs.
Why: probes are best-effort evidence over another process environment — uncertainty must not fail a health check that automation gates on, while a provably wrong registration is a real wiring fault.
How to apply: keep FAIL reserved for proven divergence or unreachability and degrade every inconclusive probe outcome to WARN with its reason.