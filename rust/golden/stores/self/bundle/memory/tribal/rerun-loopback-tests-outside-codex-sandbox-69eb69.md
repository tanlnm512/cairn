---
type: Tribal-workaround
title: Rerun loopback tests outside Codex sandbox
description: Rerun loopback tests outside Codex sandbox
resource: tests/test_embedding_backend_quality.py
tags:
- workaround
generated:
  by: cairn/0.21.1
  at: '2026-09-22T17:39:20Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-22T17:39:20Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-22T17:39:20Z'
---

Python and VS Code tests that bind 127.0.0.1 can fail with EPERM under Codex's filesystem sandbox even though the suite is healthy.\nWhy: The sandbox denies socket bind independently of application correctness, producing false failures in embedding-backend and extension hover tests.\nHow to apply: When failures cluster on socket bind, rerun the cached failures with pytest/npm outside the sandbox before diagnosing product code.