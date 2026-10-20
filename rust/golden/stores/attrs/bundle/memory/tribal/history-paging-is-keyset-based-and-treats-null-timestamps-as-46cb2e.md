---
type: Tribal-decision
title: History paging is keyset-based and treats null timestamps as pre-window
description: History paging is keyset-based and treats null timestamps as pre-window
resource: src/cairn/dashboard/data.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:38Z'
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
- date: '2026-10-07T16:57:38Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:38Z'
---

History pages order and cursor on invoked_at plus row id; unparseable cursors are ignored, and NULL invoked_at rows appear only on unwindowed, cursorless pages.
Why: NULL values never satisfy SQL comparisons, so windowing or cursoring on them would silently strand rows or repeat a page forever.
How to apply: keep numeric epoch ordering, keyset predicates, and neighbor probes scoped to the same filters as the page.