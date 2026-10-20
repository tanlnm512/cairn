---
type: Tribal-pattern
title: Claude agent prompts preload the shared skill
description: Claude agent prompts preload the shared skill
resource: src/cairn/agent_install/_common.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:10Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.628
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.93
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:10Z'
  action: captured
  score: 0.93
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:10Z'
---

Claude-format agent frontmatter sets skills to cairn so the full skill instructions are preloaded; the translated prompt remains role-specific.
Why: Claude supports skill preloading, and duplicating the whole skill in every prompt would rot quickly.
How to apply: Keep generated agent prompts focused on role behavior and rely on the named cairn skill for shared instructions.