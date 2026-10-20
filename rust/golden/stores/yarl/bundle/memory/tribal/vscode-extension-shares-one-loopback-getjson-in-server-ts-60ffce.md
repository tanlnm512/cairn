---
type: Tribal-pattern
title: vscode extension shares one loopback getJson in server.ts
description: vscode extension shares one loopback getJson in server.ts
resource: extensions/vscode/src/server.ts
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T14:30:33Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T14:30:33Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T14:30:33Z'
---

All editor-surface HTTP reads go through getJson(host, port, path, timeoutMs) in extensions/vscode/src/server.ts: parsed JSON body, or null on timeout, transport failure, or non-JSON body. hover.ts fetchSymbolAt maps null to null; compassModel.ts defaultFileFetcher maps null to a thrown Error so its latest-wins runner keeps rejecting. codeLens.ts stays on defaultFetchSymbol rather than calling getJson directly, so symbol path+parse wiring exists once.