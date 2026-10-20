---
type: Tribal-pattern
title: Red-first repin must migrate every boot snippet in the test file
description: Red-first repin must migrate every boot snippet in the test file
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-11T16:50:35Z'
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
memory_tier: archived
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-11T16:50:35Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-10-11T16:50:35Z'
---

tests/test_http_transport.py T001: migrating only the task-named rows leaves _BOOT_HTTP passing transport='http'; when T003 deletes run()'s transport kwarg (Touches: server.py only), every boot in the file TypeErrors and T003's verify anchor crashes. A red-first repinner must move ALL boots in its file to the post-change interface, accepting that keyed traffic pins go red until the implementation task lands.