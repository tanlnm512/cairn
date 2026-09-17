---
type: Tribal-mistake
title: Run the repo CLI from the repo venv, never a global cairn install
description: Run the repo CLI from the repo venv, never a global cairn install
resource: main
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T05:04:39Z'
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
- date: '2026-09-17T05:04:39Z'
  action: captured
  score: 0.8
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T05:04:39Z'
---

A global cairn binary on PATH runs the released wheel, not the working tree, so edits under `src/cairn` stay invisible to it and verification exercises a stale release. How to apply: run the repo venv entry point `main` (editable install) or uv run --no-sync cairn in CI.