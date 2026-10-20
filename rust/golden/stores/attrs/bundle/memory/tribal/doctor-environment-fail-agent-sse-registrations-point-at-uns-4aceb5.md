---
type: Tribal-mistake
title: 'doctor environment FAIL: agent SSE registrations point at unserved 127.0.0.1:9876'
description: 'doctor environment FAIL: agent SSE registrations point at unserved 127.0.0.1:9876'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T08:27:12Z'
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
- date: '2026-09-17T08:27:12Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: false
memory_supersedes:
- memory/tribal/cairndoctorssedaemondown-538d66
- memory/tribal/cairn-doctor-fail-agent-sse-registrations-without-a-serving--c26a8a
- memory/tribal/cairn-doctor-environment-fail-stale-sse-daemon-registrations-4e30e6
- memory/tribal/cairn-doctor-environment-fail-sse-registrations-without-daem-8cd3bb
memory_superseded_by: memory/raw/2026-09-17-cairn-doctor-environment-fail-sse-endpoint-127-0-0-1-9876-un-5e9218
valid_from: '2026-09-17T08:27:12Z'
---

cairn doctor exits 1: claude/zcode/agy SSE registrations in user configs point at http://127.0.0.1:9876/sse but no daemon serves it. Environmental (user-level agent configs), not repo code. Fix = start 'cairn serve start' or re-register via 'cairn install-agents --stdio'; left to the user, not side-effect of a task.