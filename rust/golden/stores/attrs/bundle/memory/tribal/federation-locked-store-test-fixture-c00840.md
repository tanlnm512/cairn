---
type: Tribal-pattern
title: Federation locked-store test fixture
description: Federation locked-store test fixture
resource: tests/test_federation.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T09:23:23Z'
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
- date: '2026-09-17T09:23:23Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T09:23:23Z'
---

WAL-mode SQLite stores ignore writer locks for readers, so a real locked-store fixture needs PRAGMA journal_mode = DELETE plus a held BEGIN EXCLUSIVE from a second connection.
Why: cairn stores run WAL (schema.get_db), where BEGIN EXCLUSIVE never blocks readers.
How to apply: hold the EXCLUSIVE transaction around the code under test, roll back in a finally; the read-only open then raises OperationalError after busy_timeout.