---
type: Tribal-workaround
title: 'MCP proofs on macOS: connect-started daemons are launchd KeepAlive — never
  bare-kill them in TCs (respawn poisons later port-guards); clean up via serve stop.
  cg_bin must prefer argv[0] over which(''cairn'') or plists launch the wrong cairn
  version (pipx 0.20.2 vs repo). Stray matcher must accept interpreter-prefixed argv
  (python3 .../cairn serve).'
description: 'MCP proofs on macOS: connect-started daemons are launchd KeepAlive —
  never bare-kill them in TCs (respawn poisons later port-guards); clean up via serve
  stop. cg_bin must prefer argv[0] over which(''cairn'') or plists launch the wrong
  cairn version (pipx 0.20.2 vs repo). Stray matcher must accept interpreter-prefixed
  argv (python3 .../cairn serve).'
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-10-12T18:43:50Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-10-12T18:43:50Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-12T18:43:50Z'
---

MCP proofs on macOS: connect-started daemons are launchd KeepAlive — never bare-kill them in TCs (respawn poisons later port-guards); clean up via serve stop. cg_bin must prefer argv[0] over which('cairn') or plists launch the wrong cairn version (pipx 0.20.2 vs repo). Stray matcher must accept interpreter-prefixed argv (python3 .../cairn serve).