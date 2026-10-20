---
type: Tribal-pattern
title: Shared memory rows are validated idempotent pointers
description: Shared memory rows are validated idempotent pointers
resource: src/cairn/memory/store.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:35Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T14:39:35Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:35Z'
---

Share rows validate agent ids and symbols before inserting, store one idempotent pointer per symbol without an existence check, and leave commit ownership with the caller; recall accepts bundle-relative or absolute memory ids and tolerates a not-yet-migrated agent_symbols table.
Why: readers join pointers to live memories so stale pointers can be ignored, while writers historically emit both id forms and older stores may lack the table.
How to apply: validate at the share boundary, normalize both id forms when reading, and never commit a caller-owned connection.