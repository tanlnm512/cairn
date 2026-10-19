---
type: Tribal-decision
title: Settings forms never reflect or clear stored API keys
description: Settings forms never reflect or clear stored API keys
resource: src/cairn/dashboard/routes/settings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:45Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:45Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:45Z'
---

The embedding API-key input prefills empty, submits only a non-empty replacement, and otherwise treats blanks as no change for all settings values.
Why: reflecting the secret leaks it to the browser and a blank form write would silently clear stored configuration.
How to apply: keep the write-only input behavior and reserve explicit config-file edits for clearing knobs.