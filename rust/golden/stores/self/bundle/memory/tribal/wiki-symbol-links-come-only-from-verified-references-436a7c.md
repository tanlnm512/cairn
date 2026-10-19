---
type: Tribal-decision
title: Wiki symbol links come only from verified references
description: Wiki symbol links come only from verified references
resource: src/cairn/dashboard/data.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:37Z'
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
- date: '2026-10-07T16:57:37Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:37Z'
---

Wiki code spans become graph deep links only when frontmatter sources or planned symbol seeds vouch for them, and map keys use the same escaped text as rendered spans.
Why: linking arbitrary backticked words would invent symbol resolutions the graph never made, while mismatched escaping breaks span lookup.
How to apply: build ref_hrefs from verified references only and keep html.escape(quote=False) on both keys and rendered text.