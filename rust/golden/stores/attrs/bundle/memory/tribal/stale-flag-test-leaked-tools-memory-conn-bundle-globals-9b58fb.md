---
type: Tribal-mistake
title: stale_flag test leaked tools_memory._conn/_bundle globals
description: stale_flag test leaked tools_memory._conn/_bundle globals
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T00:50:41Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-17T00:50:41Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T00:50:41Z'
---

tests/test_memory_stale_flag.py rebound tools_memory._conn/_bundle with raw module-attribute assignment (no restore). pytest runs modules alphabetically, so test_mcp_degradation_footnote normally runs first and the leak was hidden; any order putting stale_flag first handed later modules a _TestConn wrapping the already-teardown-closed in-memory fixture conn, surfacing as sqlite3.ProgrammingError 'Cannot operate on a closed database' from record/recall (first unguarded conn use: store.py write_validity, embeddings.py embed_memory_count). Rule: tests patching imported-into-namespace helpers (from ._server_core import _conn) must use monkeypatch.setattr on the consuming module, never raw assignment. Fixed by threading monkeypatch through _recall.