---
type: Tribal-decision
title: Incremental file replacement is transactional
description: Incremental file replacement is transactional
resource: src/cairn/graph/incremental.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:41Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:04:41Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:41Z'
---

Each file deletion, reparse, and insertion runs in one transaction with explicit commit or rollback at every exit.
Why: a crash or parse failure must leave either the complete old rows or the complete new rows.
How to apply: keep self-committing follow-ups outside the transaction and settle failed nested commits before rollback.