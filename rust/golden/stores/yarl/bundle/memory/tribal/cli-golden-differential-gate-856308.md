---
type: Tribal-decision
title: cli_golden differential gate
description: cli_golden differential gate
tags:
- decision
generated:
  by: cairn/0.22.0
  at: '2026-10-19T04:56:25Z'
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
- date: '2026-10-19T04:56:25Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T04:56:25Z'
---

scripts/rust_migration/cli_golden.py diffs live python-vs-rust CLI runs over the C-4 command set (config --json/--db/--mcp-config + knowledge list/search/islands --json) with no fixture files; --rust-bin .venv/bin/cairn self-checks the machinery (6/6 PASS). Rust stubs exit 1 with a display::error diagnostic until each command is ported, so the gate stays red by design until the 3a command workstreams land.

Why: plan.md C-4 gates machine contracts by normalized differential runs (ANSI stripped, scratch paths scrubbed via golden_record.scrub_rules); both sides must run the same scratch store snapshot.

How to apply: run it as the acceptance gate for every cairn-cli command port; a port is done only when the command flips to PASS here.