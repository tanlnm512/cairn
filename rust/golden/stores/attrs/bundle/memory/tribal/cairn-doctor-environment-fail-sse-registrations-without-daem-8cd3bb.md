---
type: Tribal-mistake
title: 'cairn doctor environment FAIL: SSE registrations without daemon'
description: 'cairn doctor environment FAIL: SSE registrations without daemon'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-16T19:53:34Z'
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
- date: '2026-09-16T19:53:34Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: false
memory_supersedes: []
memory_superseded_by: memory/tribal/cairn-doctor-environment-fail-stale-sse-daemon-registrations-4e30e6
valid_from: '2026-09-16T19:53:34Z'
---

cairn doctor exits 1 on this machine: claude/agy SSE registrations point at http://127.0.0.1:9876/sse but no daemon serves it. Unrelated to any code change — schema/embeddings/freshness/concurrency all PASS. How to apply: start the shared daemon (cairn serve start) or re-register with cairn install-agents --stdio; treat this FAIL as environmental unless it appears on other machines.