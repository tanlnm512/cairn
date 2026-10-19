---
type: Tribal-mistake
title: 'cairn doctor environment FAIL: stale SSE daemon registrations'
description: 'cairn doctor environment FAIL: stale SSE daemon registrations'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T01:53:24Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-17T01:53:24Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: false
memory_supersedes:
- memory/tribal/cairn-doctor-environment-fail-sse-registrations-without-daem-8cd3bb
memory_superseded_by: memory/tribal/cairn-doctor-fail-agent-sse-registrations-without-a-serving--c26a8a
valid_from: '2026-09-17T01:53:24Z'
---

cairn doctor exits 1 with environment FAIL: SSE registrations in ~/.claude.json, ~/.zcode/cli/config.json, and ~/.gemini/config/mcp_config.json point at http://127.0.0.1:9876/sse but no daemon serves that endpoint. Why: the doctor gate treats a registered-but-dead MCP endpoint as an active degradation even when all graph checks pass, so a task that runs doctor per AGENTS.md hygiene sees a non-zero exit unrelated to its code. How to apply: distinguish this pre-existing machine-state FAIL from task-caused FAILs (all other checks PASS) before attributing it to a change; fix by serving or removing the stale 9876 registration, which belongs to the workspace owner, not a task agent.