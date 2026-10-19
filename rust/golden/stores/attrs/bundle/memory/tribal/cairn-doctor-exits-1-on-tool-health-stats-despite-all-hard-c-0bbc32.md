---
type: Tribal-mistake
title: cairn doctor exits 1 on tool_health stats despite all hard checks PASS
description: cairn doctor exits 1 on tool_health stats despite all hard checks PASS
resource: extensions/vscode/src/providers/codeLens.ts
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T14:24:30Z'
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
- date: '2026-09-17T14:24:30Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T14:24:30Z'
---

cairn doctor can exit 1 while schema/embeddings/ann/freshness/parse_errors/concurrency all PASS: the tool_health WARN over historical CLI/MCP error rates alone trips the exit code. Why: the exit contract treats any WARN-including run as a degradation, and tool_health accumulates history from synthetic probes and help-only invocations (e.g. failing_function: 100% err, cli:main: 100% err p95 0ms). How to apply: on exit 1, read the report before concluding the tree is degraded; treat hard-check PASS + tool_health-only WARN as pre-existing stats noise and report it, not fix it.