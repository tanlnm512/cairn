---
type: Tribal-workaround
title: Run package module commands through the venv interpreter
description: Run package module commands through the venv interpreter
resource: src/cairn/cli/system/audit_status.py
tags:
- workaround
generated:
  by: cairn/0.21.2
  at: '2026-10-05T16:52:11Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-10-05T16:52:11Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-05T16:52:11Z'
---

Bare python3 -m cairn... fails on this checkout because system python3 has neither the editable install nor click; the package layout resolves only under .venv/bin/python (or PYTHONPATH=src plus the venv's deps).
Why: acceptance commands phrased as python3 -m silently test the wrong interpreter and look like module defects.
How to apply: invoke .venv/bin/python -m <module> for module acceptance, and let the Makefile wiring own the interpreter it ships.