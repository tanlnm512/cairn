---
type: Tribal-pattern
title: Kilo and omp global paths follow client discovery
description: Kilo and omp global paths follow client discovery
resource: src/cairn/agent_install/clients/kilo.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:30Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.63
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.94
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:30Z'
  action: captured
  score: 0.94
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:30Z'
---

Kilo and omp global configurations use each client’s documented user-level config locations, and detection probes those same paths.
Why: Global installs must be both consumed by the client and visible to installed-state checks.
How to apply: Update generator, detector, and uninstaller together when a client documents a new global path.