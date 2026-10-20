---
type: Tribal-pattern
title: Files-table lookups must be scoped by repo_id (path is only unique per repo)
description: Files-table lookups must be scoped by repo_id (path is only unique per
  repo)
resource: src/cairn/graph/incremental.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-30T04:13:28Z'
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
- date: '2026-09-30T04:13:28Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-30T04:13:28Z'
---

files.path is REPO-RELATIVE and only unique per (repo_id, path) (schema: UNIQUE(repo_id, path)). Any `WHERE path = ?` lookup without a repo_id predicate can return another repo's row in multi-repo workspaces and silently corrupt data through delete+reinsert legs. Every files-table lookup (reindex_paths, _find_tracked_file_row, derived-prestate capture) must scope by the repo_id already in hand. Same bug class as the compass detect_gaps path-id join (repo-qualified ids joined to repo-relative resources): repo-relative paths are NOT globally unique keys.

Why: the 2026-09-29 audit found the unscoped lookup as its one high-severity finding — incremental updates cross-corrupted repos sharing a relative path (e.g. two repos both holding src/main.py) with no error surfaced.
How to apply: when writing or reviewing any SQL against files (or other repo-relative-path tables), always pair path with repo_id; treat a bare path predicate as a red flag in multi-repo workspaces.