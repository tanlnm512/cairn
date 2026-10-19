---
type: Tribal-decision
title: Parser registry prefers built-in languages before plugins
description: Parser registry prefers built-in languages before plugins
resource: src/cairn/parsers/_registry.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:15:06Z'
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
- date: '2026-10-07T17:15:06Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:15:06Z'
---

Built-in language modules are tried before the cairn.parsers.v1 entry-point scan; the plugin scan runs only after a built-in miss.

Why: built-ins avoid an import-metadata walk on every common language while still allowing external grammars to extend the registry.
How to apply: add new bundled grammars to the built-in loader first, and reserve entry-point plugins for languages that are not shipped with Cairn.