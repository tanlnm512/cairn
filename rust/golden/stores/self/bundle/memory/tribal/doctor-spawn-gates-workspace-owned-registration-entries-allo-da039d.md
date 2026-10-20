---
type: Tribal-decision
title: Doctor spawn-gates workspace-owned registration entries (allowlist, not verbatim)
description: Doctor spawn-gates workspace-owned registration entries (allowlist, not
  verbatim)
resource: src/cairn/cli/system/doctor.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-27T16:41:38Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-09-27T16:41:38Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-27T16:41:38Z'
---

doctor's environment audit spawn-gates workspace-owned registration entries via _spawnable_workspace_entry (doctor.py:867-888): argv must be exactly [PATH-resolved cairn binary, "serve"], env keys ⊆ {CAIRN_HOME}, python -m fallback never spawnable; any other shape degrades to a WARN naming the file and skips verify_registration entirely.
Why: pre-fix, doctor spawn-executed command+args+env verbatim from workspace config files (.mcp.json, kilo.json, .cursor/.factory/.omp mcp.json, .zcode/config.json, opencode.json) — RCE from a single cloned-repo file, found by the 2026-09-27 whole-project audit (regression test test_environment_does_not_spawn_workspace_registration fails on pre-fix code).
How to apply: never loosen the gate to "spawn whatever the config names"; new client config paths added to detect.py must be added to the workspace-owned set too, and install-agents output shape changes must update the allowlist in lockstep.