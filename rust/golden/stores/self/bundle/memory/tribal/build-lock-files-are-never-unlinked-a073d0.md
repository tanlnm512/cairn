---
type: Tribal-decision
title: Build lock files are never unlinked
description: Build lock files are never unlinked
resource: src/cairn/graph/schema.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:54Z'
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
- date: '2026-10-07T14:52:54Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:54Z'
---

The advisory build lock acquires a non-blocking exclusive flock and leaves its zero-byte lock file on release.
Why: Why: flock is inode-based, and unlinking can race a third process into a fresh inode while a waiter still blocks on the old one.
How to apply: How to apply: treat stale build-lock files as harmless and never remove them as part of lock release.