---
type: Tribal-pattern
title: Token views distinguish unknown truncation from zero truncation
description: Token views distinguish unknown truncation from zero truncation
resource: src/cairn/dashboard/data.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:40Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:40Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:40Z'
---

A tool with no durable truncation evidence reports NULL truncation fields, while a real zero-evidence aggregate reports zero.
Why: a missing evidence row cannot prove that no truncation occurred, especially for rows recorded before size tracking existed.
How to apply: preserve optional truncation aggregates and render None as unknown rather than zero.