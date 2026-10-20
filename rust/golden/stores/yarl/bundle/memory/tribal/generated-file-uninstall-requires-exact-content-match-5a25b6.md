---
type: Tribal-decision
title: Generated-file uninstall requires exact content match
description: Generated-file uninstall requires exact content match
resource: src/cairn/agent_install/merge.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:19Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.64
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.99
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:19Z'
  action: captured
  score: 0.99
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:19Z'
---

Uninstall removes a generated file only when its bytes exactly equal current installer output; mismatches are recorded as skipped.
Why: Install deliberately does not overwrite pre-existing files, so a same-named user file or older generated variant is not necessarily ours to delete.
How to apply: Pair every generated file with its expected bytes for uninstall and leave mismatches reported rather than deleted.