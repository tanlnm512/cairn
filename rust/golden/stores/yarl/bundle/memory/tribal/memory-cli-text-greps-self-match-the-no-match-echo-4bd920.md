---
type: Tribal-mistake
title: Memory CLI text greps self-match the no-match echo
description: Memory CLI text greps self-match the no-match echo
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-16T21:12:41Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-16T21:12:41Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-16T21:12:41Z'
---

memory search/timeline stdout on a miss echoes 'No memories matching <query>.' — any pass-condition grep for the bare query string self-matches it and false-passes negative legs. Anchor greps to the result-row shape (leading-whitespace bracket-score prefix) so rows and echoes are distinguishable.