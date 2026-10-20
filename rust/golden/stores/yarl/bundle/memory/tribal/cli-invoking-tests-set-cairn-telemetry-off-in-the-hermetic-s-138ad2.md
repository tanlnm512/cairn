---
type: Tribal-pattern
title: CLI-invoking tests set CAIRN_TELEMETRY=off in the hermetic sandbox
description: CLI-invoking tests set CAIRN_TELEMETRY=off in the hermetic sandbox
resource: src/cairn/cli/ask_context.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T09:44:20Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T09:44:20Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T09:44:20Z'
---

CLI tests that dispatch the click group must set CAIRN_TELEMETRY=off inside the sandbox. record_cli_invocation starts the shared metrics flusher, and its drain calls the conn factory at process exit, resolving a store from the then-current env. Why: buffered rows survive the sandbox and resolve against the real home after tests restore env. How to apply: set the kill switch wherever a test enters a hermetic sandbox before invoking cairn.cli.main; see tests/test_federation.py FederatedAskTests._enter_sandbox.