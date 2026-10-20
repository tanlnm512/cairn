---
type: Tribal-mistake
title: get_db side-pass must not resolve knowledge from env default
description: get_db side-pass must not resolve knowledge from env default
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-16T19:53:08Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-16T19:53:08Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-16T19:53:08Z'
---

A connect-time pass inside get_db initially resolved the knowledge bundle via paths.resolve_store().knowledge while the DB could be a --db-overridden path: an acceptance CLI run with --db tmp/.kg then backfilled the process-default workspace bundle into the tmp DB (and stamped foreign concept files). Why: the CLI's --knowledge flag never reaches get_db. How to apply: derive companion paths from the db path argument (sibling <db dir>/.knowledge); pin with a no-foreign-reach regression test (tests/test_schema_versioning.py::test_memory_validity_backfill_records_sentinel_without_sibling).