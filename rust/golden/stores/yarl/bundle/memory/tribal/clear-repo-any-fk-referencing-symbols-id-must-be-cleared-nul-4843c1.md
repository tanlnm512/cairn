---
type: Tribal-mistake
title: '_clear_repo: any FK referencing symbols(id) must be cleared/null first (imports.resolved_symbol_id
  still latent)'
description: '_clear_repo: any FK referencing symbols(id) must be cleared/null first
  (imports.resolved_symbol_id still latent)'
resource: src/cairn/graph/builder.py
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-27T16:41:39Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-27T16:41:39Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-27T16:41:39Z'
---

_clear_repo (builder.py) must clear every table whose rows FK-reference symbols(id) before DELETE FROM symbols: embeddings_mv was missed and crashed any `cairn build --repo` on a multivector-embedded repo (fixed 2026-09-27 by adding DELETE FROM embeddings_mv in step 3b). Still uncleared: incoming cross-repo imports.resolved_symbol_id (schema.py:64 REFERENCES symbols(id), no cascade) — incremental.py:130 nulls it but _clear_repo does not. A hand-planted resolved import row reproduces IntegrityError; builder.py:1006 always inserts None today, so cairn's own builds never produce the state.
Why: same latent trap as the mv bug — any future writer of resolved_symbol_id would crash single-repo rebuilds identically.
How to apply: when adding a table that references symbols(id), add its delete/null to BOTH _clear_repo and the incremental path; probe with PRAGMA foreign_keys=ON on an init_db connection before trusting a rebuild test.