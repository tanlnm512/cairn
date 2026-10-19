---
type: Tribal-decision
title: OpenCode global config is read and detectable
description: OpenCode global config is read and detectable
resource: src/cairn/agent_install/clients/opencode.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:26Z'
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
- date: '2026-10-07T17:50:26Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:26Z'
---

OpenCode global installation writes ~/.config/opencode/opencode.json, the same path the client reads and check_installed probes.
Why: A global path that is written but neither read nor detected produces an integration that appears missing after installation.
How to apply: Keep generator, detector, and uninstall path tables synchronized for each scope.