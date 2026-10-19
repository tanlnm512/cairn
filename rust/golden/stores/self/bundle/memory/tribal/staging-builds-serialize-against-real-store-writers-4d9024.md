---
type: Tribal-decision
title: Staging builds serialize against real-store writers
description: Staging builds serialize against real-store writers
resource: src/cairn/cli/core.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:16:07Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T16:16:07Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:16:07Z'
---

A staged graph build holds <db>.build.lock — the same lock single-repo builds and incremental updates take, not <db>.tmp.build.lock — across build, derived indexes, and the database swap.
Why: concurrent non-staging writers write the live DB while the staging build writes the temp copy; without the shared lock their writes would be silently clobbered by the swap.
How to apply: keep the lock held for the whole staging build and never introduce a staging-only lock file.