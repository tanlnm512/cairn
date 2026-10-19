---
type: Tribal-pattern
title: Registration probe timeouts avoid healthy-install flakes
description: Registration probe timeouts avoid healthy-install flakes
resource: src/cairn/agent_install/__init__.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:03Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.63
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.94
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:03Z'
  action: captured
  score: 0.94
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:03Z'
---

The registration probe timeout is intentionally generous relative to a warm process start because timeout is reported as failure.
Why: A healthy installation must not fail verification merely because a cold interpreter or model import exceeds a typical warm duration.
How to apply: Treat timeout ceiling changes as reliability changes and validate them on cold and loaded systems.