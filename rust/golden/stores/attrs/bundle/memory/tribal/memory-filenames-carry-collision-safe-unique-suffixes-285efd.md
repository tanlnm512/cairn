---
type: Tribal-decision
title: Memory filenames carry collision-safe unique suffixes
description: Memory filenames carry collision-safe unique suffixes
resource: src/cairn/memory/store.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:34Z'
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
- date: '2026-10-07T14:39:34Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:34Z'
---

Memory tiers use UUID suffixes, consolidation uses a Unicode-preserving non-truncating slugifier, and archival captures the old filename before changing a concept id.
Why: same-day or same-title captures otherwise overwrite through atomic file replacement, and ASCII truncation changes stable consolidated concept ids.
How to apply: every tier-move filename needs collision isolation; preserve identifier text when slugifying and remove the old file only after the new concept is written.