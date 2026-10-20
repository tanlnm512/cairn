---
type: Tribal-decision
title: Ingest identities remain distinct under the slug cap
description: Ingest identities remain distinct under the slug cap
resource: src/cairn/knowledge/ingest/identity.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:19Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:19Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:19Z'
---

Stable ids keep the full slug when it fits; overlong paths gain a non-security digest anchor, and duplicate display slugs gain numbered repo suffixes until fresh in the sorted run.
Why: slug truncation can merge distinct paths and a repeated slug would make one staged document overwrite another.
How to apply: share slugify's character class, keep prefixes and suffixes within the cap, and pass one accumulating slug set through sorted rows.