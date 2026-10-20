---
type: Tribal-mistake
title: 'Doctor environment FAIL: stale SSE client registrations'
description: 'Doctor environment FAIL: stale SSE client registrations'
resource: /Users/tanle/.zcode/cli/config.json
tags:
- mistake
generated:
  by: cairn/0.22.0
  at: '2026-10-19T09:49:28Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0.0
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
- date: '2026-10-19T09:49:28Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes:
memory_superseded_by: null
valid_from: '2026-10-19T09:49:28Z'
---

cairn doctor FAILs the environment check: agy (~/.gemini/config/mcp_config.json) and zcode (~/.zcode/cli/config.json) registrations point at http://127.0.0.1:9876/sse but the daemon serves http://127.0.0.1:9876/mcp (pre-HTTP-era endpoint). Why: the clients were wired before the stdio/SSE-to-HTTP transport switch and were never repointed. How to apply: run `cairn install-agents` or `cairn doctor --fix` to rewrite the registrations to the URL+key form; agents gating on doctor exit codes will see the FAIL until then.