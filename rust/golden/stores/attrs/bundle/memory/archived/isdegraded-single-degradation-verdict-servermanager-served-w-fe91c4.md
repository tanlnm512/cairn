---
type: Tribal-pattern
title: isDegraded single degradation verdict + ServerManager served watch
description: isDegraded single degradation verdict + ServerManager served watch
resource: extensions/vscode/src/statusModel.ts
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T13:57:52Z'
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
memory_tier: archived
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T13:57:52Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-09-17T13:57:52Z'
---

The VS Code extension shell (extensions/vscode) derives all degradation from one aggregate verdict: statusModel.isDegraded(states) — true while the worst root is offline, connecting, or unindexed. Hover/lens/panel surfaces (T005-T007) must read this export instead of re-deriving degradation per provider. ServerManager arms a post-settle health watch (watchIntervalMs, default 5s) on every served binding, so an adopted server that stops answering also degrades to offline — without it, attach-first sessions never notice a stopped server. How to apply: new inline-data surfaces consult isDegraded and render nothing when it is true; server-state consumers rely on the served watch rather than adding their own polling.