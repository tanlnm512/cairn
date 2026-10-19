---
type: Tribal-decision
title: Generic-tier call edges remain unresolved
description: Generic-tier call edges remain unresolved
resource: src/cairn/parsers/generic_tree_sitter.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-15T17:44:21Z'
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
- date: '2026-09-15T17:44:21Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T17:44:21Z'
---

Generic-tier parsers emit call edges marked generic_tier, and resolution preserves target_name while forcing resolution=unresolved.\nWhy: generic grammars provide names but not full-fidelity binding semantics, so unique names are not proof of an exact target.\nHow to apply: extend GenericTreeSitterParser for grammar-only languages; do not add receiver or import bindings that promote its calls to exact.