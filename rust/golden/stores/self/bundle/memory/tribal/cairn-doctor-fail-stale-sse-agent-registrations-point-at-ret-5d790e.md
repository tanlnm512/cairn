---
type: Tribal-mistake
title: 'cairn doctor FAIL: stale /sse agent registrations point at retired endpoint'
description: 'cairn doctor FAIL: stale /sse agent registrations point at retired endpoint'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-18T18:59:47Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-18T18:59:47Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: false
memory_supersedes:
- memory/tribal/doctor-environment-fail-stale-sse-client-registrations-0e5d82
- memory/tribal/cairn-doctor-fail-stale-sse-daemon-registration-on-127-0-0-1-041478
- memory/raw/2026-09-17-cairn-doctor-environment-fail-sse-endpoint-127-0-0-1-9876-un-5e9218
- memory/tribal/doctor-environment-fail-agent-sse-registrations-point-at-uns-4aceb5
- memory/tribal/cairndoctorssedaemondown-538d66
- memory/tribal/cairn-doctor-fail-agent-sse-registrations-without-a-serving--c26a8a
- memory/tribal/cairn-doctor-environment-fail-stale-sse-daemon-registrations-4e30e6
- memory/tribal/cairn-doctor-environment-fail-sse-registrations-without-daem-8cd3bb
memory_superseded_by: memory/tribal/doctor-fail-stale-sse-agent-registrations-predate-http-mcp-e-35a89b
valid_from: '2026-10-18T18:59:47Z'
---

`cairn doctor` exits 1: the zcode (~/.zcode/cli/config.json) and agy (~/.gemini/config/mcp_config.json) registrations still point at http://127.0.0.1:9876/sse while the daemon serves /mcp, so the environment check fails.
Why: pre-existing workspace degradation, unrelated to the Rust migration edits; the sanctioned fix (`cairn doctor --fix` / `cairn install-agents`) rewrites user-owned config files outside the repo, which a scoped workstream must not do unasked.
How to apply: run `cairn doctor --fix` (or `cairn install-agents`) before trusting MCP-based hygiene checks here; re-run `cairn doctor` and expect exit 0 once repointed.