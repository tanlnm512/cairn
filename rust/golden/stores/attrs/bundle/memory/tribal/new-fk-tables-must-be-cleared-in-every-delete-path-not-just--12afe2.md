---
type: Tribal-mistake
title: New FK tables must be cleared in every delete path, not just the incremental
  one
description: New FK tables must be cleared in every delete path, not just the incremental
  one
resource: src/cairn/graph/builder.py
tags:
- mistake
generated:
  by: cairn/0.21.2
  at: '2026-10-09T16:27:55Z'
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
- date: '2026-10-09T16:27:55Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-09T16:27:55Z'
---

The rationale table landed with incremental-path cleanup but _clear_repo (the repo-scoped rebuild path) still deleted symbols/files first, so 'cairn build --repo' raised IntegrityError on any repo containing NOTE/WHY/HACK comments and left a permanent building marker (audit C78).\n\nWhy: each new table with FK references and no cascade multiplies the delete-ordering surface across builder.py's incremental, repo-scoped, and full-rebuild paths; only one was tested.\n\nHow to apply: when adding a symbols/files-referencing table, grep every DELETE FROM symbols|files call site and add the child-table clear beside each; ship a failing-first test per delete path.