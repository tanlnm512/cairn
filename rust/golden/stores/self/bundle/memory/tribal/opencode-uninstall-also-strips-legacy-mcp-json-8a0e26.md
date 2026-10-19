---
type: Tribal-workaround
title: OpenCode uninstall also strips legacy mcp.json
description: OpenCode uninstall also strips legacy mcp.json
resource: src/cairn/agent_install/merge.py
tags:
- workaround
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:20Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:20Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:20Z'
---

OpenCode teardown removes .opencode/mcp.json in addition to opencode.json when present.
Why: Earlier installers wrote that file even though OpenCode does not read it, so ordinary config removal leaves stale integration debris.
How to apply: Keep the targeted legacy cleanup until no supported upgrade path can still contain the old file.