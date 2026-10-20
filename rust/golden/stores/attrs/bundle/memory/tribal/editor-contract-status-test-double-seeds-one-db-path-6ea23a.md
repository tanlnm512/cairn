---
type: Tribal-mistake
title: editor contract status test double-seeds one db path
description: editor contract status test double-seeds one db path
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T14:06:22Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-17T14:06:22Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T14:06:22Z'
---

tests/test_dashboard_editor_contract.py::test_status_endpoint_reports_indexed_state calls _client(tmp_path, rows=True) then _client(tmp_path, rows=False) but _seed_store always uses tmp_path/editor.db and the rows=False pass is non-destructive, so the second client reads the same populated file (sha256-identical) and the unindexed zero-shape pin is unsatisfiable. Fix belongs in the test: seed the schema-only store at a distinct path (or unlink first).