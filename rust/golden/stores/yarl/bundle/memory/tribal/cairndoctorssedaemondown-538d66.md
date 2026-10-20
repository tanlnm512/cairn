---
type: Tribal-mistake
title: CairnDoctorSseDaemonDown
description: CairnDoctorSseDaemonDown
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T05:47:44Z'
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
- date: '2026-09-17T05:47:44Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: false
memory_supersedes:
- memory/tribal/cairn-doctor-fail-agent-sse-registrations-without-a-serving--c26a8a
- memory/tribal/cairn-doctor-environment-fail-stale-sse-daemon-registrations-4e30e6
- memory/tribal/cairn-doctor-environment-fail-sse-registrations-without-daem-8cd3bb
memory_superseded_by: memory/tribal/doctor-environment-fail-agent-sse-registrations-point-at-uns-4aceb5
valid_from: '2026-09-17T05:47:44Z'
---

cairn doctor exits 1 with FAIL environment: claude/zcode/agy registrations in user config files point at http://127.0.0.1:9876/sse but no daemon serves it. Pre-existing machine state, unrelated to repo code changes. Fix per hint: cairn serve start, or re-register with cairn install-agents --stdio. Do not fix by spawning daemons from task agents.