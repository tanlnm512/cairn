---
type: Tribal-pattern
title: Dashboard htmx routes share one seam and honor history restores
description: Dashboard htmx routes share one seam and honor history restores
resource: src/cairn/dashboard/app.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:26Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:26Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:26Z'
---

One route and data fetch render the full template on page loads and the fragment variant on HX-Request requests; HX-History-Restore-Request forces the full page.
Why: browser history restores carry HX-Request but must restore the shell rather than swap a detached fragment.
How to apply: branch through is_hx_request and keep page and fragment rendering on the same data-fetch seam.