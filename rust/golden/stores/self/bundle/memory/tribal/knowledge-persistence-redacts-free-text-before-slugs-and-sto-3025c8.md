---
type: Tribal-decision
title: Knowledge persistence redacts free text before slugs and storage
description: Knowledge persistence redacts free text before slugs and storage
resource: src/cairn/knowledge/store.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:14Z'
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
- date: '2026-10-07T17:31:14Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:14Z'
---

Knowledge persistence redacts title, body, description, and step descriptions before slug derivation and bundle writes; relationship, verified-ref, and step graph identifiers remain exact.
Why: secret-shaped free text must not reach markdown, filenames, or embeddings, while graph anchors must remain resolvable.
How to apply: route every create and sync path through the same redaction boundary and leave identifiers verbatim.