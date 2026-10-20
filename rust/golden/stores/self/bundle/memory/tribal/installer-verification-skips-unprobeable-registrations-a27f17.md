---
type: Tribal-pattern
title: Installer verification skips unprobeable registrations
description: Installer verification skips unprobeable registrations
resource: src/cairn/agent_install/__init__.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:00Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.634
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.96
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:00Z'
  action: captured
  score: 0.96
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:00Z'
---

Install verification spawn-probes only file-written stdio registrations; SSE URL registrations and CLI-registered clients remain skipped with explanatory notes, and dry-run never spawns.
Why: URL registrations have no local argv to validate and CLI-only clients lack a file-written registration in the current run.
How to apply: Add newly supported registration kinds to the explicit probe/skip classification rather than letting them fail generically.