---
type: Tribal-pattern
title: Session bookkeeping progress counts apply to titles only
description: Session bookkeeping progress counts apply to titles only
resource: src/cairn/memory/promotion.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:29Z'
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
- date: '2026-10-07T14:39:29Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:29Z'
---

Task-id, branch, and dated-count bookkeeping patterns scan title plus body, while bare progress counts scan only the title.
Why: memory bodies legitimately contain statements such as counts of callers or work remaining, which are not session bookkeeping.
How to apply: add bookkeeping patterns with an explicit title/body scope and cover false-positive body counts in tests.