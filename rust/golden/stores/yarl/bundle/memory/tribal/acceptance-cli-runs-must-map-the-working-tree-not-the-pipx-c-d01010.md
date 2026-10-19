---
type: Tribal-workaround
title: Acceptance CLI runs must map the working tree, not the pipx copy
description: Acceptance CLI runs must map the working tree, not the pipx copy
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-16T19:53:21Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-16T19:53:21Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-16T19:53:21Z'
---

The cairn binary on PATH (~/.local/bin/cairn) is a pipx install with a non-editable site-packages copy — it does not run this repo's working tree. How to apply: run acceptance/CLI commands via the repo .venv (editable install, imports src/cairn): .venv/bin/cairn ... or PYTHONPATH=<repo>/src prepended. Otherwise verification exercises a stale release (0.20.2), not the branch.