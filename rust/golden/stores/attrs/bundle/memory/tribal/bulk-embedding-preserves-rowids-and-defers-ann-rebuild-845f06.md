---
type: Tribal-decision
title: Bulk embedding preserves rowids and defers ANN rebuild
description: Bulk embedding preserves rowids and defers ANN rebuild
resource: src/cairn/graph/embeddings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:42Z'
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
- date: '2026-10-07T14:52:42Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:42Z'
---

Bulk upserts use ON CONFLICT DO UPDATE so existing embeddings rowids survive, leave per-row ANN sync omitted, and rebuild the wholesale index afterward; orphan reaping before multi-vector writes only affects dead symbols.
Why: Why: INSERT OR REPLACE or per-row vec0 sync would orphan index keys or make large corpus passes pay a delete-plus-insert per row.
How to apply: How to apply: keep bulk paths rowid-stable and realign vec0 through one wholesale rebuild.