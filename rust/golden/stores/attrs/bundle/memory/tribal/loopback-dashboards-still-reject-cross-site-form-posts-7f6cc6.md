---
type: Tribal-decision
title: Loopback dashboards still reject cross-site form posts
description: Loopback dashboards still reject cross-site form posts
resource: src/cairn/dashboard/routes/settings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:45Z'
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
- date: '2026-10-07T16:57:45Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:45Z'
---

Dashboard POST handlers reject mismatched Sec-Fetch-Site or Origin/Referer origins, do not trust same-site, and allow requests carrying no browser origin context.
Why: loopback binding does not prevent a web page from submitting a CORS-safelisted form to localhost, while another localhost port counts as same-site.
How to apply: guard every browser-reachable mutating route with _reject_cross_site and leave GET handlers unguarded.