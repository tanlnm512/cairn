---
type: Tribal-workaround
title: Fixture review-loop tests need /usr/bin-first PATH
description: Fixture review-loop tests need /usr/bin-first PATH
resource: tests/fixtures/review-loop/prepare.sh
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-17T03:59:02Z'
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
- date: '2026-09-17T03:59:02Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: false
memory_supersedes: []
memory_superseded_by: memory/tribal/review-loop-fixture-tests-need-working-git-ahead-on-path-09fba3
valid_from: '2026-09-17T03:59:02Z'
---

tests/test_review_loop_fixtures.py builds git fixtures via prepare.sh; a stale /usr/local/bin/git (wrong CPU arch) breaks every scenario with 'Bad CPU type in executable'. Run pytest with PATH="/usr/bin:$PATH" so Apple git resolves first.