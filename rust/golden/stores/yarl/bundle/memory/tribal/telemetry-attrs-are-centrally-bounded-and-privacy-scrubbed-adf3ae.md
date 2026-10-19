---
type: Tribal-decision
title: Telemetry attrs are centrally bounded and privacy-scrubbed
description: Telemetry attrs are centrally bounded and privacy-scrubbed
resource: src/cairn/telemetry/events.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:24:00Z'
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
- date: '2026-10-07T14:24:00Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:24:00Z'
---

Every telemetry attr string, including nested and stringified values, passes strip_private_data and a 500-character cap; containers have structural caps; serialization failure or a serialized blob over 4000 characters becomes NULL attrs while the event survives.
Why: attrs reach both the events table and the optional OTLP network path, and exception text routinely contains secrets or absolute paths that callers cannot be trusted to scrub.
How to apply: route new attrs through _coerce_attrs, prefer enums and short tags, and preserve the event when its attrs must be dropped.