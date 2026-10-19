---
type: Tribal-pattern
title: Skill packages install as complete trees
description: Skill packages install as complete trees
resource: src/cairn/agent_install/merge.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:16Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:16Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:16Z'
---

Skill installation recursively copies SKILL.md plus references, scripts, and evals while preserving per-file force, dry-run, and executable-bit semantics.
Why: Treating the skill as a package avoids hand-picked files drifting from the source bundle and leaves scripts runnable.
How to apply: Add new skill-package files to the source tree rather than special-casing them in installers.