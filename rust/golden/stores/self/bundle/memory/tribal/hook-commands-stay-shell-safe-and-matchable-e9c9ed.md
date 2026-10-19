---
type: Tribal-decision
title: Hook commands stay shell-safe and matchable
description: Hook commands stay shell-safe and matchable
resource: src/cairn/agent_install/_common.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:09Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.638
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.98
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:09Z'
  action: captured
  score: 0.98
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:09Z'
---

Hook strings quote the home and interpreter with shlex, prefix CAIRN_HOME only when non-default, and retain the module-qualified cairn.hooks entrypoint marker.
Why: Clients execute hooks through a shell, so paths must not split or inject, while uninstall and idempotence match on the stable marker.
How to apply: Preserve quoting and the marker whenever changing hook command generation.