---
type: Tribal-workaround
title: review-loop fixture tests need working git ahead on PATH
description: review-loop fixture tests need working git ahead on PATH
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-17T04:10:55Z'
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
- date: '2026-09-17T04:10:55Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes:
- memory/tribal/fixture-review-loop-tests-need-usr-bin-first-path-22de2e
memory_superseded_by: null
valid_from: '2026-09-17T04:10:55Z'
---

tests/test_review_loop_fixtures.py shells out to git via prepare.sh; /usr/local/bin/git is x86-only on this machine and fails with 'Bad CPU type'. Run pytest with /usr/bin first on PATH (e.g. env PATH=/usr/bin:/bin:/usr/sbin:/sbin).