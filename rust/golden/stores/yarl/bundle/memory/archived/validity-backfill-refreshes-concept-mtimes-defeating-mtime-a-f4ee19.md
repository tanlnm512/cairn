---
type: Tribal-mistake
title: Validity backfill refreshes concept mtimes defeating mtime-aged fixtures
description: Validity backfill refreshes concept mtimes defeating mtime-aged fixtures
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-16T21:22:05Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-16T21:22:05Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-09-16T21:22:05Z'
---

Fact: get_db runs the memory_validity backfill which rewrites every memory concept file in the paired bundle, refreshing file mtimes; tests that age a concept file by mtime (e.g. tests/test_doctor.py test_memory_staleness_warns_on_write_only_memory seeds 40d then opens the DB) see a fresh mtime and the mtime-based staleness check flips WARN to PASS. Why: seeding must happen after the DB open, or the check needs a non-mtime age signal. How to apply: re-seed or re-age the file after get_db in mtime-sensitive fixtures; currently failing on the temporal-memory branch, pre-dating T011.