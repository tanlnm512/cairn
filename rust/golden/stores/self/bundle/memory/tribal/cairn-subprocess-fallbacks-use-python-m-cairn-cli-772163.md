---
type: Tribal-decision
title: Cairn subprocess fallbacks use python -m cairn.cli
description: Cairn subprocess fallbacks use python -m cairn.cli
resource: src/cairn/cli/__main__.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-08T15:55:37Z'
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
- date: '2026-10-08T15:55:37Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-08T15:55:37Z'
---

Agent-launched cairn subprocesses use python -m cairn.cli when no binary is on PATH, and hook runners surface non-zero exits as error strings.\n\nWhy: The package entrypoint executes the fully registered Click group without runpy re-import warnings.\n\nHow to apply: Keep cairn/cli/__main__.py aligned with the cairn.cli:main entrypoint and check subprocess return codes before returning output.