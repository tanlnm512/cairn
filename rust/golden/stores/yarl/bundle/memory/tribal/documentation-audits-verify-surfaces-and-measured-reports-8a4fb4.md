---
type: Tribal-decision
title: Documentation audits verify surfaces and measured reports
description: Documentation audits verify surfaces and measured reports
resource: docs/benchmarks.md
tags:
- decision
generated:
  by: cairn/0.21.1
  at: '2026-09-22T17:36:44Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-09-22T17:36:44Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-22T17:36:44Z'
---

Documentation freshness checks compare the installed CLI command set, registered MCP tools, parser registry, optional extras, and current benchmark JSON before editing prose.\nWhy: Static prose can remain link-valid while its counts, task shapes, and measured medians no longer describe the executable product.\nHow to apply: Introspect Click/FastMCP and run the relevant benchmark or eval, then update docs and rerun check_doc_links plus the normal gates.