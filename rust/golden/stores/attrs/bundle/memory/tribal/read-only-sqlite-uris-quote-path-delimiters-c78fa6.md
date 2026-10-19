---
type: Tribal-decision
title: Read-only SQLite URIs quote path delimiters
description: Read-only SQLite URIs quote path delimiters
resource: src/cairn/graph/schema.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:52Z'
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
- date: '2026-10-07T14:52:52Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:52Z'
---

Read-only graph opens URI-quote the filesystem path and require the database file to exist.
Why: Why: question marks, hashes, and spaces otherwise parse as URI components and can silently truncate the mode=ro target.
How to apply: How to apply: every new read-only SQLite URI must quote its path and intentionally decide how a missing file is reported.