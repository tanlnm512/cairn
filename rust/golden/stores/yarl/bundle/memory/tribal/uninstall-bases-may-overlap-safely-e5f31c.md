---
type: Tribal-pattern
title: Uninstall bases may overlap safely
description: Uninstall bases may overlap safely
resource: src/cairn/agent_install/_common.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:08Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:08Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:08Z'
---

Workspace-first uninstall bases may overlap because every strip helper is idempotent.
Why: The all scope must cover both historical workspace and global installations without duplicate-removal errors.
How to apply: Keep newly added uninstall operations idempotent before exposing them to multiple bases.