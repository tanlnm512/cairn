---
type: Tribal-pattern
title: Dashboard health probes serve stale while revalidating in the background
description: Dashboard health probes serve stale while revalidating in the background
resource: src/cairn/dashboard/data.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:33Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:33Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:33Z'
---

Expensive health probes are cached process-wide, prewarmed from a daemon, revalidated after TTL in the background, and cold callers wait only a bounded period before receiving unknown values.
Why: probe imports can dominate the dashboard request budget, but direct callers still need a synchronous fallback.
How to apply: route probe consumers through _serve_probes and prewarm at app creation rather than importing probe dependencies eagerly.