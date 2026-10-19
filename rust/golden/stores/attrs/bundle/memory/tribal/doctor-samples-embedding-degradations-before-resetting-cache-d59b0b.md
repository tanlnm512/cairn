---
type: Tribal-decision
title: Doctor samples embedding degradations before resetting caches
description: Doctor samples embedding degradations before resetting caches
resource: src/cairn/cli/system/doctor.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:29:02Z'
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
- date: '2026-10-07T16:29:02Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:29:02Z'
---

The embed-server check reads any active ladder degradation before calling reset_backend_cache(), which drops the cached probe, stamp resolution, and session adoptions; it then probes the configured server live. Non-server backends do no network I/O so default output stays byte-stable.
Why: resetting first would erase the degradation doctor exists to report, while keeping the cache would report a stale verdict for a server backend.
How to apply: preserve the read-then-reset order when extending the check, and keep zero-row parity samples vacuously passing for fresh installs.