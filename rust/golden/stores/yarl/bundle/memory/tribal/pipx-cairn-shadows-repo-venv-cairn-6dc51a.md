---
type: Tribal-mistake
title: Pipx cairn shadows repo venv cairn
description: Pipx cairn shadows repo venv cairn
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-16T21:21:44Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-16T21:21:44Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes:
- memory/raw/2026-09-16-pipx-cairn-intel-install-is-stale-vs-repo-venv-f18b0a
memory_superseded_by: null
valid_from: '2026-09-16T21:21:44Z'
---

Fact: /Users/tanle/.local/bin/cairn is a pipx install of released cairn-intel and lacks in-repo changes; the repo dev entry point is /Users/tanle/Projects/cairn/.venv/bin/cairn. Why: acceptance commands invoking bare 'cairn' silently hit the stale pipx build and fail with usage error 2. How to apply: prefix repo .venv/bin onto PATH before running cairn acceptance commands in this workspace.