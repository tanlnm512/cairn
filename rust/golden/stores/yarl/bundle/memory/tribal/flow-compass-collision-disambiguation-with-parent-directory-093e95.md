---
type: Tribal-pattern
title: Flow compass collision disambiguation with parent directory
description: Flow compass collision disambiguation with parent directory
resource: src/cairn/compass/flow_gaps.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-18T16:36:06Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-18T16:36:06Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-18T16:36:06Z'
---

When candidate symbols collide across files, use basename suffix name#fname unless basenames also collide, in which case include parent directory segment name#parent/fname.
Why: Maintains backward compatibility with existing concept IDs while guaranteeing unique resource keys for identical basenames in different directories.
How to apply: In detect_flow_gaps, count occurrences of each basename per colliding symbol name before building resources.