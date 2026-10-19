---
type: Tribal-workaround
title: macOS stale x86 git fallback in fixtures
description: macOS stale x86 git fallback in fixtures
resource: tests/fixtures/review-loop/prepare.sh
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-18T16:35:40Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-18T16:35:40Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-18T16:35:40Z'
---

When PATH prioritizes /usr/local/bin over /usr/bin on Apple Silicon, an obsolete x86_64 git can cause Bad CPU type errors in subprocess fixtures. Fall back to /usr/bin/git if default git fails.
Why: Prevents environment-specific test fixture failures on migrated macOS setups.
How to apply: In shell fixtures or subprocess callers, verify git --version succeeds before invoking git commands or prepend /usr/bin to PATH.