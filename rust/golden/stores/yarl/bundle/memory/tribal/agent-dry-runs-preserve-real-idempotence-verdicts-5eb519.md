---
type: Tribal-pattern
title: Agent dry runs preserve real idempotence verdicts
description: Agent dry runs preserve real idempotence verdicts
resource: src/cairn/agent_install/merge.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:15Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.636
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.97
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:15Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:15Z'
---

Existence, match, and malformed-backup checks run before dry-run short-circuits.
Why: A dry-run report must describe what a real run would do; checking only “would write” turns existing or unsafe files into phantom writes.
How to apply: Add new preflight classifications before the dry-run branch and mirror their real-run result notes.