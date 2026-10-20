---
type: Tribal-decision
title: cairn rationale --json rows are helper dicts plus a path key
description: cairn rationale --json rows are helper dicts plus a path key
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-05T12:26:22Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.6
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.8
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-05T12:26:22Z'
  action: captured
  score: 0.8
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-10-05T12:26:22Z'
---

`cairn rationale --json` emits the graph.rationale helper row dicts (file_id, symbol_id, line, kind, text) with one added `path` key (repo-relative, joined from files). Text rendering is `path:LINE [kind] text`, sorted by (path, line, kind, text). Unknown --symbol / no records exit 0 with "No rationale records found.".
Why: FR-003 output must identify the file; the read helpers do not carry path, so the CLI joins it rather than duplicating SQL against the rationale table.
How to apply: T009 docs should document `path` in the JSON schema; T007's explore Rationale section should use the same kind-tag style for consistency; consumers must not assume the helper dicts are returned unmodified.