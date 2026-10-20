---
type: Tribal-decision
title: SCIP edges resolve by definition occurrence positions
description: SCIP edges resolve by definition occurrence positions
resource: src/cairn/parsers/scip_importer.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:15:14Z'
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
- date: '2026-10-07T17:15:14Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:15:14Z'
---

SCIP edge resolution joins an occurrence to the definition occurrence at its recorded position; an unjoinable definition is dropped and counted rather than matched by name.

Why: name-keyed fallbacks can silently bind stale or duplicate definitions; position joins expose real indexer disagreement.
How to apply: keep exact-position joins authoritative and record join misses as anomalies instead of introducing name-based recovery.