---
type: Tribal-mistake
title: Test.md proofs commands must be one line and escape-safe
description: Test.md proofs commands must be one line and escape-safe
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T08:15:42Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-17T08:15:42Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T08:15:42Z'
---

The spec-to-prod proofs parser reads the pass-condition line only: continuation lines are never appended (the post-match remainder is always empty), so a backticked TC command must open and close on that single line. re.sub replacement strings process backslash escapes, so commands containing printf escape sequences must be inserted via a lambda repl. Shell single-quoted args keep their inner spacing; flattening whitespace when joining args across display lines corrupts embedded indentation.