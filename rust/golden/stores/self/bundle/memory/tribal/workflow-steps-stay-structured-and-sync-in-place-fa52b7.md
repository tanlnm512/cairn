---
type: Tribal-decision
title: Workflow steps stay structured and sync in place
description: Workflow steps stay structured and sync in place
resource: src/cairn/knowledge/workflow.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:17Z'
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
- date: '2026-10-07T17:31:17Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:17Z'
---

The structured steps extension is authoritative over the rendered body; tracing returns doc_status even for archived workflows; sync reuses the existing concept id and reapplies redaction to generated free text.
Why: rendering can diverge, a title-derived id would relocate the workflow, and status policy belongs to the caller.
How to apply: never recover steps by parsing the rendered body, overwrite by the existing concept id, and redact on every write path.