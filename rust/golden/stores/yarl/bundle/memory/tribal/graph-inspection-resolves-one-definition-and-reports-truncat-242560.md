---
type: Tribal-pattern
title: Graph inspection resolves one definition and reports truncation honestly
description: Graph inspection resolves one definition and reports truncation honestly
resource: src/cairn/dashboard/data.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:31Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:31Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:31Z'
---

Inspect picks the first same-name symbol by file and id, follows resolved edges for direct neighbors, and reports neighbor and impact truncation flags plus same-name count.
Why: same-name definitions are common and silently hiding ambiguity or caps makes the panel appear exhaustive.
How to apply: keep deterministic ordering, explicit caps, and resolved-only edges when extending the payload.