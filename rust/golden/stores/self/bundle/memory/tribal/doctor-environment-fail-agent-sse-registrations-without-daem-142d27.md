---
type: Tribal-mistake
title: 'Doctor environment FAIL: agent SSE registrations without daemon'
description: 'Doctor environment FAIL: agent SSE registrations without daemon'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-16T20:11:01Z'
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
- date: '2026-09-16T20:11:01Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-16T20:11:01Z'
---

cairn doctor exits 1 with 'claude/zcode/agy: SSE registration in endpoint does not respond (no daemon is serving it)'. Pre-existing environment state, not caused by the temporal-memory wave-5 diff; fix is 'cairn serve start' or 'cairn install-agents --stdio', an environment action owned by the main session, not a subagent.