---
type: Tribal-decision
title: Memory validity backfill design
description: Memory validity backfill design
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-16T19:52:37Z'
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
- date: '2026-09-16T19:52:37Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-16T19:52:37Z'
---

Connect-time one-time backfill (_maybe_backfill_memory_validity in schema.py, sentinel memory_validity.backfill in schema_meta) stamps pre-feature memory concepts with valid_from = concept.timestamp. The frontmatter timestamp is the recorded creation date — it survives copies and checkouts where file mtime does not; promotion decay already treats it as the temporal anchor. The bundle is derived as the sibling <db dir>/.knowledge of the opened DB (paths.py StorePaths pairs them) — never a process-default resolve_store() path, because get_db receives --db overrides that resolve_store() cannot see. How to apply: any future per-DB companion-bundle pass must derive paths from the db path, not env defaults.