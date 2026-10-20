---
type: Tribal-workaround
title: Pinned sed line-range anchors drift when additive DDL lands upstream
description: Pinned sed line-range anchors drift when additive DDL lands upstream
resource: src/cairn/graph/schema.py
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-17T00:27:56Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-17T00:27:56Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T00:27:56Z'
---

Acceptance commands pinning line ranges (sed -n '837,855p' src/cairn/graph/schema.py for WAL/busy_timeout) land on unrelated code after an upstream task inserts DDL above them.
Why: the pragma code did not move or change; only its line numbers shifted with additive schema text above it.
How to apply: verify pragma presence by grep (PRAGMA journal_mode = WAL, PRAGMA busy_timeout) rather than a pinned range, and re-pin ranges only when the substrate itself changes.