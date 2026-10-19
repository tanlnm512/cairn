---
type: Tribal-decision
title: No-refresh graph queries report probe drift
description: No-refresh graph queries report probe drift
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-15T17:54:59Z'
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
- date: '2026-09-15T17:54:59Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T17:54:59Z'
---

CAIRN_NO_REFRESH, --no-refresh, and read-only server mode preserve the stored graph and return a FreshnessReport banner derived from _detect_changed. The banner has a deterministic drifted count and capped path list; clean trees produce no banner.