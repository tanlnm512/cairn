---
type: Tribal-pattern
title: Hook ownership matching spans client shapes and legacy paths
description: Hook ownership matching spans client shapes and legacy paths
resource: src/cairn/agent_install/merge.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:18Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.638
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.98
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:18Z'
  action: captured
  score: 0.98
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:18Z'
---

Hook matching recognizes Claude nested and Cursor flat entries through module-qualified entrypoint markers, including the legacy src.hooks spelling.
Why: Ownership depends on the stable hook module marker, not the absolute Python path or client-specific nesting used when a file was written.
How to apply: Keep marker-based matching for idempotence and uninstall instead of comparing generated command prefixes.