---
type: Tribal-mistake
title: Reusing a variable for setdefault before a get assignment broke Optional typing
  in blast.py
description: Reusing a variable for setdefault before a get assignment broke Optional
  typing in blast.py
resource: src/cairn/graph/blast.py
tags:
- mistake
generated:
  by: cairn/0.21.2
  at: '2026-10-09T14:50:01Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-09T14:50:01Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-09T14:50:01Z'
---

Binding 'seed' via seeds.setdefault() (non-Optional dict) ahead of the existing seed = seeds.get() (dict | None) assignment failed CI mypy; the local pre-commit hook set has no mypy, so the regression surfaced only after push.\n\nWhy: mypy infers the variable type from first binding; pre-commit gates formatting/ruff/secrets but not types.\n\nHow to apply: when touching annotated code, run '.venv/bin/python -m mypy --ignore-missing-imports src' before pushing; use distinct variable names when the Optional and non-Optional lookup shapes differ.