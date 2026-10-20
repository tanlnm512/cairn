---
type: Tribal-decision
title: Stray serve kills require anchored cmdlines and verified DB ownership
description: Stray serve kills require anchored cmdlines and verified DB ownership
resource: src/cairn/mcp_server/lifecycle.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:47Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:35:47Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:47Z'
---

A pid qualifies as a stray only when its cmdline is a real serve invocation, it is outside the protected set (self, launchd daemon, daemon children), and lsof verifies it actually holds the target db open; if lsof verification is impossible, nothing is killed.
Why: orphans holding the WAL lock are the root cause of database-is-locked errors, but an unverifiable kill is worse than a missed sweep pass, and a foreground server on a different db is never ours to kill.
How to apply: keep all three gates; surface a skip message when verification fails.