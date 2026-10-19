---
type: Tribal-decision
title: Agent installs default to shared SSE transport
description: Agent installs default to shared SSE transport
resource: src/cairn/agent_install/__init__.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:06Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.636
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.97
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:06Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:06Z'
---

Install defaults clients to the shared SSE daemon while Claude Desktop always receives stdio.
Why: A shared daemon avoids per-client server processes, but Claude Desktop has no SSE support.
How to apply: Preserve the desktop exception whenever changing the default transport and keep the daemon probe advisory rather than transport-selecting.