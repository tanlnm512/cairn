---
type: Tribal-mistake
title: Shell git only with a working binary ahead on PATH
description: Shell git only with a working binary ahead on PATH
resource: git
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

A broken or foreign-arch git first on PATH (an x86-only build on Apple silicon, for example) fails every shelled call with a Bad CPU type error. How to apply: put a working git first on PATH wherever code shells git, such as `_run_git` in `src/cairn/utils/git.py` and the review-loop fixture scripts.