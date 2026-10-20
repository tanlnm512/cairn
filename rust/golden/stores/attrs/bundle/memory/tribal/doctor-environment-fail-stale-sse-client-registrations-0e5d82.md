---
type: Tribal-mistake
title: 'doctor environment FAIL: stale /sse client registrations'
description: 'doctor environment FAIL: stale /sse client registrations'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-18T17:27:53Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.6
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.8
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-18T17:27:53Z'
  action: captured
  score: 0.8
  tier: tribal
memory_is_latest: false
memory_supersedes:
- memory/tribal/cairn-doctor-fail-stale-sse-daemon-registration-on-127-0-0-1-041478
- memory/raw/2026-09-17-cairn-doctor-environment-fail-sse-endpoint-127-0-0-1-9876-un-5e9218
- memory/tribal/doctor-environment-fail-agent-sse-registrations-point-at-uns-4aceb5
- memory/tribal/cairndoctorssedaemondown-538d66
- memory/tribal/cairn-doctor-fail-agent-sse-registrations-without-a-serving--c26a8a
- memory/tribal/cairn-doctor-environment-fail-stale-sse-daemon-registrations-4e30e6
- memory/tribal/cairn-doctor-environment-fail-sse-registrations-without-daem-8cd3bb
memory_superseded_by: memory/tribal/cairn-doctor-fail-stale-sse-agent-registrations-point-at-ret-5d790e
valid_from: '2026-10-18T17:27:53Z'
---

`cairn doctor` exits 1 on the cairn workspace: the environment check reports that ~/.zcode/cli/config.json and ~/.gemini/config/mcp_config.json still register the cairn MCP server at http://127.0.0.1:9876/sse while the daemon serves http://127.0.0.1:9876/mcp (pre-HTTP-era endpoint). State observed 2026-10-18 during P0-5; no repo code involved.
Why: the daemon migrated from SSE to streamable HTTP but those user-owned client registrations were never rewritten; `cairn install-agents` (or `cairn doctor --fix`) is the sanctioned fix and it mutates user config files.
How to apply: if doctor's environment FAIL blocks a ship gate, run `cairn install-agents` (user-approved action, not agent-default) rather than hand-editing the configs; the failure is workspace-environment state, not a regression signal for code changes.