---
type: Tribal-decision
title: Default scan skips keep generated code out of the graph
description: Default scan skips keep generated code out of the graph
resource: src/cairn/graph/scanner.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:10Z'
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
- date: '2026-10-07T15:05:10Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:10Z'
---

Default directory skips apply even without gitignore and cover build output, VCS, dependencies, caches, generated web output, IDE state, and Cairn stores.
Why: the graph should describe hand-written source rather than vendored or generated noise.
How to apply: match default directory names case-insensitively on the final component and treat additions as indexing-scope changes.