---
type: Tribal-decision
title: Grade-a ratchet uses content-counted baselines
description: Grade-a ratchet uses content-counted baselines
resource: specs/grade-a-ratchet/tech-spec.md
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-05T16:22:20Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-05T16:22:20Z'
  action: captured
  score: 0.94
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-05T16:22:20Z'
---

The comment ratchet keys its allowlist by violation kind plus normalized content and stores occurrence counts rather than path/line entries.\nWhy: Path and line keys churn on moves or unrelated insertions, while content-only sets miss duplicate growth; exact count comparison supports a shrink-only update mode.\nHow to apply: Keep baseline initialization absent-file-only, make updates refuse new fingerprints or higher counts, and recompute counts from the current src tree instead of trusting documented totals.