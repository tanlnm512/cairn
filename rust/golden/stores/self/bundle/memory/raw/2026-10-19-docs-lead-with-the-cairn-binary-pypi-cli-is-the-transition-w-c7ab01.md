---
type: Tribal-decision
title: Docs lead with the cairn binary; PyPI CLI is the transition-window full surface
description: Docs lead with the cairn binary; PyPI CLI is the transition-window full surface
resource: docs/cli-reference.md
tags:
- decision
generated:
  by: cairn/0.22.0
  at: '2026-10-19T09:49:37Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0.0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: raw
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-19T09:49:37Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes:
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-10-19T09:49:37Z'
---

The 3e docs sweep (README.md, docs/cli-reference.md, docs/deployment.md, docs/architecture.md, AGENTS.md CLI-fallback) documents the Rust binary as the install path while keeping the PyPI CLI as the complete command surface for the transition window. Why: the binary ports the core graph, daemon, and agent-wiring commands plus knowledge/memory reads, and stub-exits with a named diagnostic for anything else (verified by dispatch-arm reading plus 52 stub probes and 22 covered-command probes against the release binary). How to apply: consult the binary-coverage block at the top of docs/cli-reference.md before scripting a fallback command; a command outside it needs the PyPI CLI until its port lands; do not claim post-flip PyPI state (security-fix-only) before the flip.