---
type: Tribal-workaround
title: Codex imported Cairn hooks need quoting and retrust
description: Codex imported Cairn hooks need quoting and retrust
resource: src/cairn/hooks/claude_hooks.py
tags:
- workaround
generated:
  by: cairn/0.21.1
  at: '2026-09-27T06:17:29Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.638
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.98
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-27T06:17:29Z'
  action: captured
  score: 0.98
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-27T06:17:29Z'
---

Codex user hooks live in ~/.codex/hooks.json and execute through a shell; imported Claude/Cairn commands must quote the interpreter path and keep stdout protocol-clean.\n\nWhy: pipx's macOS path contains a space, and Codex parses hook stdout as JSON. Codex also records trust against each exact hook definition hash.\n\nHow to apply: quote the interpreter, redirect Cairn 0.20.2 post_edit/session_end diagnostics to stderr, then run /hooks in the Codex CLI to review and trust the changed definitions.