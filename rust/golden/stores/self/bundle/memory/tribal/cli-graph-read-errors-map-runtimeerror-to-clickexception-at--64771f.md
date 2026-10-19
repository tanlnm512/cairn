---
type: Tribal-decision
title: CLI graph-read errors map RuntimeError to ClickException at the boundary
description: CLI graph-read errors map RuntimeError to ClickException at the boundary
resource: src/cairn/cli/query.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-15T15:36:15Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-15T15:36:15Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-15T15:36:15Z'
---

Query-family CLIs convert strict-refresh and git RuntimeErrors to click.ClickException in query._refresh_conn and the blast/review handlers; BlastBaseError subclasses RuntimeError so one handler covers both. Why: refresh_for_query(repair default-on) raises bare RuntimeError on any reindex failure, which escaped as tracebacks on every read command. How to apply: new graph-reading commands call _refresh_conn or wrap compute paths in except RuntimeError -> ClickException; keep exit code 1 to match the empty-result discipline.