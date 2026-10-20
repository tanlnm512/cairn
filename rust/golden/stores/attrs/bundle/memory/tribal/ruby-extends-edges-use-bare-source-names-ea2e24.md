---
type: Tribal-pattern
title: Ruby extends edges use bare source names
description: Ruby extends edges use bare source names
resource: src/cairn/parsers/ruby.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:15:11Z'
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
- date: '2026-10-07T17:15:11Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:15:11Z'
---

Ruby extends edges use the declaring class or module bare name as source_name, even for nested declarations.

Why: same-file edge resolution keys on the bare symbol name; a qualified source leaves nested-class inheritance edges unresolved.
How to apply: retain qualified names on symbols for display, but emit inheritance source_name through the bare name contract.