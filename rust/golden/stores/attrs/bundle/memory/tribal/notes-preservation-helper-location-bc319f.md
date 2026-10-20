---
type: Tribal-decision
title: Notes preservation helper location
description: Notes preservation helper location
resource: src/cairn/compass/critic.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-15T17:35:02Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-09-15T17:35:02Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T17:35:02Z'
---

The exact ## Notes extract/splice helpers live in cairn.compass.critic, the shared critic layer already used by wiki and compass generation.\nWhy: both writer families share one section parser while critic_concept evaluates the same Notes-free body contract.\nHow to apply: wiki and compass writers must call preserved_concept_notes instead of adding another markdown section parser.