---
type: Tribal-mistake
title: Fresh-store ranged update dropped the first indexed file on a missing repos
  row
description: Fresh-store ranged update dropped the first indexed file on a missing
  repos row
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-05T09:02:40Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-05T09:02:40Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-05T09:02:40Z'
---

files.repo_id -> repos.id is enforced even on a never-built store, and the first ranged update (diff_ref <empty-tree>..HEAD) is the first writer there: insert_files raised FOREIGN KEY constraint failed for the first file, which the reindex error path converted into a parse_errors row — silently dropping exactly one file while every later file indexed fine (the freshly-inserted repos row from insert_parse_error satisfied them).

Why: only the parse-error path ensured the repos row existed; the parsed-file insert assumed a build had registered it, which is false for hook-first workflows (cairn init --no-build then a ranged hook update).

How to apply: any insert that writes a row referencing repos.id must go through builder.ensure_repo_row first (idempotent ON CONFLICT DO NOTHING); don't assume a repos row exists because indexing is running. Regression: tests/test_incremental_derived.py::test_fresh_store_range_update_indexes_all_files.