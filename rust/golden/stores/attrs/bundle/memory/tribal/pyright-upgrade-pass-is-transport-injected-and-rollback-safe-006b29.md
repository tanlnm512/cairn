---
type: Tribal-decision
title: pyright upgrade pass is transport-injected and rollback-safe
description: pyright upgrade pass is transport-injected and rollback-safe
resource: src/cairn/graph/lsp.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-15T18:15:25Z'
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
- date: '2026-09-15T18:15:25Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T18:15:25Z'
---

The optional build pass selects only ambiguous Python call edges, upgrades a single LSP location that maps to one innermost stored symbol, and wraps changes in a savepoint so server failure rolls back only that pass.\nWhy: Pyright improves precision without changing default builds, and a dying server must not leave partial upgrades or fail indexing.\nHow to apply: Keep transport injection at upgrade_ambiguous_edges; add server behavior in the transport, not in the builder.