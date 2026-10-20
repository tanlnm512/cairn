---
type: Tribal-decision
title: Change detection covers untracked and gitless repos
description: Change detection covers untracked and gitless repos
resource: src/cairn/graph/incremental.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:47Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:04:47Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:47Z'
---

Flagless detection combines tracked git diffs, untracked source files, and a files-table size/mtime fallback; diff-ref mode returns only the requested span.
Why: new files and repositories without usable git state otherwise look unchanged, while ref queries must not mix worktree signals.
How to apply: pass the open connection when stat fallback is required and keep git failures non-fatal.