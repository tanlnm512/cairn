---
type: Tribal-decision
title: Stray serve detection anchors on argv tokens, not substrings
description: Stray serve detection anchors on argv tokens, not substrings
resource: src/cairn/mcp_server/lifecycle.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:45Z'
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
- date: '2026-10-07T15:35:45Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:45Z'
---

A cmdline counts as a serve server only when the argv[0] basename is cairn, argv[1] is exactly serve, and argv[2] is not a lifecycle subcommand (start/stop/status/restart).
Why: editors spawn plain cairn serve (stdio) and launchd runs cairn serve run --port N; substring scans false-positive on grep cairn serve, an editor open on a server file, or cairn build, and lifecycle subcommands are transient CLI invocations, never servers.
How to apply: pgrep -f is only the deliberately over-matching superset; always run the anchored per-pid token check before acting.