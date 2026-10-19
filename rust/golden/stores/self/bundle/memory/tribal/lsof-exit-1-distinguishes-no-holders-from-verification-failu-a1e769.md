---
type: Tribal-pattern
title: lsof exit 1 distinguishes no-holders from verification failure
description: lsof exit 1 distinguishes no-holders from verification failure
resource: src/cairn/mcp_server/lifecycle.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:46Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:35:46Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:46Z'
---

_db_holder_pids treats lsof exit 1 with empty stderr as a legitimate empty holder set, but exit 1 with stderr (or any other failure, timeout, or missing binary) as None meaning verification impossible.
Why: conflating the two either kills nothing ever or treats cannot-verify as nobody-holds-it; callers must never read None as an empty set.
How to apply: keep the (returncode, stderr) pair check when shelling out to lsof -F p.