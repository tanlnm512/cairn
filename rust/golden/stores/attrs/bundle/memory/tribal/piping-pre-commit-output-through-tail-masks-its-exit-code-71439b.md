---
type: Tribal-mistake
title: Piping pre-commit output through tail masks its exit code
description: Piping pre-commit output through tail masks its exit code
resource: Makefile
tags:
- mistake
generated:
  by: cairn/0.21.2
  at: '2026-10-06T15:44:28Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-06T15:44:28Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-06T15:44:28Z'
---

Running 'pre-commit run --all-files | tail' makes the pipeline exit code tail's zero, so a red Layer-0 gate reads as green and the commit proceeds; in this repo core.hooksPath also bypasses pre-commit at commit time, so nothing else catches it. Always capture pre-commit output to a file, test the real exit status, then read the tail.