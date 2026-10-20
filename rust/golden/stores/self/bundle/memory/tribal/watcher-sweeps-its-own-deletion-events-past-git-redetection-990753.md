---
type: Tribal-pattern
title: Watcher sweeps its own deletion events past git redetection
description: Watcher sweeps its own deletion events past git redetection
resource: src/cairn/graph/watcher.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-15T15:36:39Z'
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
- date: '2026-10-15T15:36:39Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-15T15:36:39Z'
---

FileWatcherService._update_pass reindexes deleted event paths via reindex_paths under schema.build_lock after incremental_update, because git redetection (diff HEAD + ls-files --others) never lists a deleted untracked file. Why: ghost files/symbols rows and stuck edges otherwise persist until a strict refresh. How to apply: any new watcher-side update path must pass the event's own paths to a path-targeted repair, not rely on git-based redetection.