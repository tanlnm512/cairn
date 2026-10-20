---
type: Tribal-decision
title: 'Steal campaign 8/8 specs executed and delivered in PR #148'
description: 'Steal campaign 8/8 specs executed and delivered in PR #148'
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-05T14:45:59Z'
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
- date: '2026-10-05T14:45:59Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-05T14:45:59Z'
---

All 8 steal-campaign specs EXECUTED and DELIVERED 2026-10-05 on specs/steal-campaign-2026-10 in PR #148 (retitled feat): on-demand-paths 0fa4f3d, symbol-communities 7905dd2, freshness-hooks 4937446, http-mcp-serving bdbfe29, memory-stance-overlay fb89eae, rationale-nodes 591bf92, pr-tooling 2b3515e, bench-worked-artifacts bf42d8f. Each spec: implementer waves → qa TC-repair (prose conditions → executable) → proofs all-auto green → scope/clean/DoD → tick → C1 feat + C2 delivery record. Full regression 3,977 passed / 0 failed. Tool count 26; dashboard 15 nav / 17 crawl; closure now --with-closure opt-in; parser _walk hoisted (11 copies deleted); CI gained a container job + graph-analytics extra on test legs.

**Why:** single-PR campaign delivery per user instruction; the spec-to-prod execute mode (waves, one proof pass per spec, rulings as D-###s) held at 8-spec scale.

**How to apply:** reuse the campaign mechanics for future batches — standing qa repair pass IS needed (every original qa spawn wrote prose pass conditions; audit.py executes backticked fragments verbatim), task.md markers must sit AFTER the T-id or tick.py misses them, freeze.py --record --force after sanctioned test.md repairs, and pre-commit needs `uv pip install ruff==0.16.2` in the venv (uv run ruff spawns only if installed).