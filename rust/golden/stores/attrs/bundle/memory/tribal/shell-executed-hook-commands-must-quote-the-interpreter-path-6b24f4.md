---
type: Tribal-mistake
title: Shell-executed hook commands must quote the interpreter path
description: Shell-executed hook commands must quote the interpreter path
resource: src/cairn/agent_install/_common.py
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-27T05:41:47Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.638
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.98
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-27T05:41:47Z'
  action: captured
  score: 0.98
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-27T05:41:47Z'
---

Hook command strings executed through /bin/sh must pass the interpreter through shlex.quote before embedding it in JSON settings.\n\nWhy: pipx's macOS interpreter path contains "Application Support"; unquoted, /bin/sh splits it at the space and every hook exits 127. Installer idempotence also compared only hook entrypoints, so reinstallation kept the stale broken command.\n\nHow to apply: Quote every executable/path embedded in a shell command, and make hook reinstall compare expected commands then replace old Cairn entries instead of preserving them.