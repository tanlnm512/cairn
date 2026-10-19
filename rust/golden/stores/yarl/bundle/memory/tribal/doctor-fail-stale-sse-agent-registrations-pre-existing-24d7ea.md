---
type: Tribal-mistake
title: doctor FAIL stale sse agent registrations pre-existing
description: doctor FAIL stale sse agent registrations pre-existing
tags:
- mistake
generated:
  by: cairn/0.22.0
  at: '2026-10-19T08:43:20Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-19T08:43:20Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T08:43:20Z'
---

Doctor exits 1 on this machine: the environment check flags zcode and agy registrations pointing at the /sse endpoint while the daemon serves /mcp. Fix is cairn install-agents (rewrites user-owned configs), outside the 3b dashboard workstream's file scope. Recorded as 3b post-task hygiene; the degradation predates it.
Why: AGENTS.md requires a recorded mistake on any doctor non-zero exit.
How to apply: run cairn install-agents (or doctor --fix) before the next daemon-flagged release; do not attribute this FAIL to the dashboard port.