---
type: Tribal-mistake
title: pipx cairn-intel install is stale vs repo .venv
description: pipx cairn-intel install is stale vs repo .venv
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-16T21:01:02Z'
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
memory_tier: archived
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-16T21:01:02Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: false
memory_supersedes: []
memory_superseded_by: memory/tribal/pipx-cairn-shadows-repo-venv-cairn-6dc51a
memory_triage: session-bookkeeping
valid_from: '2026-09-16T21:01:02Z'
memory_refs_baseline: 1.0
memory_stance: preferred
---

Bare `cairn` on PATH resolves to the pipx cairn-intel 0.20.2 site-packages copy (/Users/tanle/.local/bin/cairn), which predates the temporal-memory T005 changes (search_memory has no as_of); the repo's editable install is /Users/tanle/Projects/cairn/.venv/bin/cairn. Why: acceptance commands and audits invoking bare `cairn` silently test stale code. How to apply: prefix PATH with /Users/tanle/Projects/cairn/.venv/bin (or reinstall pipx package) before running cairn acceptance commands in this repo.