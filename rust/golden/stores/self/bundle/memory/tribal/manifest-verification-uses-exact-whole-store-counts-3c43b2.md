---
type: Tribal-decision
title: Manifest verification uses exact whole-store counts
description: Manifest verification uses exact whole-store counts
resource: src/cairn/knowledge/ingest/executor.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:22Z'
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
- date: '2026-10-07T17:31:22Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:22Z'
---

Manifest verification expects exactly pre-existing plus accepted minus overwritten documents, validates OKF conformance in-process, and smoke-searches; verify faults become failed report legs rather than crashes.
Why: a minimum count masks under-writes, a batch-only count fails healthy incremental runs, and subprocess validation is unnecessary.
How to apply: pass the executor's pre-write state and return False legs instead of raising.