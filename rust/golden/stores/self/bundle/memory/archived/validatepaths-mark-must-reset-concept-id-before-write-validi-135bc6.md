---
type: Tribal-workaround
title: ValidatePaths mark must reset concept_id before write_validity
description: ValidatePaths mark must reset concept_id before write_validity
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-16T20:10:21Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-16T20:10:21Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-09-16T20:10:21Z'
---

OKFBundle.read_concept keys the concept by its resolved absolute file path, so on symlinked roots (e.g. /var -> /private/var) write_validity's lexical _rel_id cannot normalize it and upserts a duplicate memory_validity row under the absolute key. Restore the bundle-relative id from the critic entry (c.concept_id = cid) before writing. Same latent pattern exists in schema.py::_maybe_backfill_memory_validity, which was out of T009 scope.