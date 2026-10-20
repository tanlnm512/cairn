---
type: Tribal-decision
title: Knowledge draft-gate prose-status bypass is load-bearing
description: Knowledge draft-gate prose-status bypass is load-bearing
resource: src/cairn/knowledge/ingest/classifier.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-15T15:38:01Z'
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
- date: '2026-10-15T15:38:01Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-15T15:38:01Z'
---

Fact/rule: do not gate prose statuses (e.g. 'Superseded by ADR-0009') in ingest/classifier.py's draft gate. Why: detect_supersede_relationships (ingest/adr.py) only scans entries with skip_reason None and deliberately re-scans parsed.status via _marker_text; the pinned test test_status_line_marker_is_detected requires a prose-superseded ADR to stay accepted so both supersedes edges are recorded. How to apply: any prose-status gating belongs in the staging layer (adr.py/staging.py) alongside supersede detection, never as a classifier-only skip.