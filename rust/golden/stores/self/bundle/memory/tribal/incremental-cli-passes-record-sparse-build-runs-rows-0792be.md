---
type: Tribal-pattern
title: Incremental CLI passes record sparse build_runs rows
description: Incremental CLI passes record sparse build_runs rows
resource: src/cairn/cli/embed.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:21:09Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T16:21:09Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:21:09Z'
---

The embed and sync commands each record their own build_runs row best-effort with only the counts their pass produces (embed: symbols/skipped; sync: files reindexed/deleted); scan, parse, resolution, and phase-timing columns stay NULL.
Why: these passes have no scan/parse/resolve phases, and each command owning its row keeps ✓ Updated: reindexed 62 files across 1 repos
! 7 memories reference file/symbol(s) that no longer fully resolve after this 
update -- verify before relying on them:
!   memory/drafts/find-symbol-paths-fo[REDACTED_SECRET]
!   memory/drafts/mcp-tool-keyword-only-params-are-schema-safe-48e15b
!   
memory/drafts/spec-code-review-saved-workflow-has-args-base-compile-error-35d004
!   
memory/raw/2026-10-03-archify-chrome-must-point-at-playwright-s-chrome-for-testi
ng-27cede
!   
memory/raw/2026-10-03-neighbor-rows-dedupe-dropped-the-caller-fuzzy-flag-a26a28
!   ... and 2 more's separate 'incremental' row honest.
How to apply: new non-scan commands should record their own kinded row with only meaningful columns, relying on record_build_run swallowing errors.