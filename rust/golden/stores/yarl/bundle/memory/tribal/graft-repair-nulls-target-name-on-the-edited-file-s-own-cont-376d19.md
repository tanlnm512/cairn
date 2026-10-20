---
type: Tribal-pattern
title: Graft repair nulls target_name on the edited file's own contains edges
description: Graft repair nulls target_name on the edited file's own contains edges
resource: rust/crates/cairn-build/tests/incremental_graft.rs
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-19T02:54:10Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-19T02:54:10Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

reindex_paths's repair pass re-resolves ANY repo edge whose target_name is in the changed-name set, including the re-parsed file's own born-resolved contains edges, and a resolved update writes target_name=NULL; a full rebuild inserts contains edges with the name kept. Why: verified by running the Python graft (reindex_paths) and a Python rebuild on a probe corpus and diffing kg_dump output - edited-file contains edges carry the null name after graft, names after rebuild; every other structural table key-compares equal. How to apply: any graft-vs-rebuild dump equality check must normalize target_name of edges that have a non-NULL target_id (derivable from target_id; unresolved edges keep the load-bearing name). See rust/crates/cairn-build/tests/incremental_graft.rs normalize_dump.