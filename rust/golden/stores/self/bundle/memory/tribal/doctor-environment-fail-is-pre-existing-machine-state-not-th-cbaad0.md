---
type: Tribal-mistake
title: Doctor environment FAIL is pre-existing machine state, not the 3d flip
description: Doctor environment FAIL is pre-existing machine state, not the 3d flip
tags:
- mistake
generated:
  by: cairn/0.22.0
  at: '2026-10-19T09:53:02Z'
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
- date: '2026-10-19T09:53:02Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T09:53:02Z'
---

The flip-era doctor run FAILs only the environment check: stale SSE-era registrations in ~/.zcode/cli/config.json and ~/.gemini/config/mcp_config.json point at /sse while the daemon serves /mcp; the diff of the flip touches no registration URL.
Why: the failure pattern-matches to a regression in the daemon work but is machine-local client config predating the change; rewriting user agent configs from a workflow subagent is out of workstream scope.
How to apply: treat this doctor FAIL as pre-existing until /install-agents is run by the human; verify against the diff before blaming a daemon change.