---
type: Tribal-decision
title: Knowledge lexical expansion trusts extracted and derived edges only
description: Knowledge lexical expansion trusts extracted and derived edges only
resource: src/cairn/knowledge/search.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:12Z'
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
- date: '2026-10-07T17:31:12Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:12Z'
---

Only extracted and derived knowledge edges expand lexical hits, at a floored fraction of the parent score; inferred edges never boost and index failures return no expansion.
Why: critic-approved inferred links are lower trust, and expansion is a bonus rather than a required index read.
How to apply: keep the expansion-kind allowlist and degrade unreadable indexes to unexpanded results.