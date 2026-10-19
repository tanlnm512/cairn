---
type: Tribal-decision
title: MemoryTimeline reads extensions not projection
description: MemoryTimeline reads extensions not projection
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-16T21:21:31Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-09-16T21:21:31Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-16T21:21:31Z'
---

Fact: cairn memory timeline renders validity window, successor link, and tier/score from concept extensions, the validity source of truth, and orders by extensions valid_from (timestamp fallback). It never queries the memory_validity projection: the cite-scan already reads every memory concept, so the SQL hop would add a missing-row failure mode with no ordering win. Why: the projection is derived and rebuildable; a bundle copied without its DB must still timeline. How to apply: keep timeline conn-free; if a temporal aggregate over the projection is added later, it is a separate SQL consumer.