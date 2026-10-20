---
type: Tribal-decision
title: Queued memory extraction redacts before persistence
description: Queued memory extraction redacts before persistence
resource: src/cairn/cli/memory.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:24:35Z'
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
- date: '2026-10-07T16:24:35Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:24:35Z'
---

When no agent session is live, memory capture queues a memory-extract task whose .md file persists the transcript-derived facts dict inside the knowledge bundle; the transcript is redacted before the task is created.
Why: the persisted task file outlives the process, so truncation alone would keep any secret it carries intact.
How to apply: run strip_private_data before any queueing or persistence step in new memory-capture paths.