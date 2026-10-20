---
type: Tribal-mistake
title: port 9876 squatted by non-cairn listener; managed daemon not loaded
description: port 9876 squatted by non-cairn listener; managed daemon not loaded
tags:
- mistake
generated:
  by: cairn/0.22.0
  at: '2026-10-18T20:10:54Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-18T20:10:54Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T20:10:54Z'
---

Port 9876 answers with 404 on /healthz and launchctl has no dev.cairn.mcp service: a non-cairn process holds the port, so the reuse-guard and the doctor daemon check correctly report no cairn daemon. Stale /sse registrations in ~/.zcode/cli/config.json and ~/.gemini/config/mcp_config.json keep cairn doctor at exit 1 (environment FAIL).
Why: a port squatter looks like a wedged daemon; and the doctor exit gate treats the stale registrations as an active degradation.
How to apply: before debugging connect/daemon health on this machine, identify the 9876 listener (lsof -i :9876); rewrite the stale registrations with cairn install-agents to restore doctor exit 0.