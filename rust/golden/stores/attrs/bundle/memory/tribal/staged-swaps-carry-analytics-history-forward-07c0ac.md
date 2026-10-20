---
type: Tribal-pattern
title: Staged swaps carry analytics history forward
description: Staged swaps carry analytics history forward
resource: src/cairn/cli/core.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:16:09Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T16:16:09Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:16:09Z'
---

Before a staged build swaps its temp DB over the live one, build_runs, events, and tool_metrics rows are copied from the DB being replaced, inside the build lock.
Why: the staged build only records its own build_runs row into the temp DB; analytics history would otherwise be lost on every staged rebuild.
How to apply: extend the copy list when a new analytics table is added, and keep the copy inside the build lock.