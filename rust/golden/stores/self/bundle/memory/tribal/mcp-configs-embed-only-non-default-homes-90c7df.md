---
type: Tribal-pattern
title: MCP configs embed only non-default homes
description: MCP configs embed only non-default homes
resource: src/cairn/agent_install/_common.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:08Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:08Z'
  action: captured
  score: 0.98
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:08Z'
---

Generated stdio MCP entries include CAIRN_HOME only when it resolves outside the default home.
Why: Explicit env pins a custom store while omitting it keeps default configurations clean and invariant under home spelling.
How to apply: Apply the same conditional to every client generator and test default plus custom homes.