---
type: Tribal-decision
title: Droid MCP registrations have a file fallback
description: Droid MCP registrations have a file fallback
resource: src/cairn/agent_install/clients/droid.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:24Z'
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
- date: '2026-10-07T17:50:24Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:24Z'
---

Droid install prefers droid mcp add and falls back to .factory/mcp.json when the CLI is unavailable or fails; uninstall strips both locations.
Why: Droid reads both registration stores, so the fallback keeps installation usable while ensuring later cleanup cannot leave the CLI registration stale.
How to apply: Preserve paired add/remove behavior and report dry-run as the action a real run would take.