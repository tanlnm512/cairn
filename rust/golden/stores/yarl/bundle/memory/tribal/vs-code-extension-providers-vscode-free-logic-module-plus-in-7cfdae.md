---
type: Tribal-pattern
title: 'VS Code extension providers: vscode-free logic module plus injected editor
  API'
description: 'VS Code extension providers: vscode-free logic module plus injected
  editor API'
resource: extensions/vscode/src/providers/codeLens.ts
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T14:24:43Z'
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
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T14:24:43Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T14:24:43Z'
---

Extension provider modules under extensions/vscode/src keep every testable concern in a module with no runtime vscode import: pure logic over the frozen /editor/symbol shapes, node:http fetch via hover.ts exports (parseSymbolPayload / defaultFetchSymbol), and the platform API injected as type-only `typeof import("vscode")` so node --test can import the module. Why: package.json's test script runs plain Node (node --test out/test/*.test.js); a runtime require("vscode") in an imported module crashes the whole test file. How to apply: new providers (e.g. the compass/memory panel) reuse hover.ts's fetch/parse exports instead of a second GET-JSON copy, and take the vscode API through a wiring object rather than importing it.