---
type: Tribal-workaround
title: ARCHIFY_CHROME must point at Playwright's Chrome for Testing binary
description: ARCHIFY_CHROME must point at Playwright's Chrome for Testing binary
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-10-03T16:37:27Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.285
memory_signals:
  graph_verification: 0.0
  cross_session_refs: 0
  agent_confidence: 1.0
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-10-03T16:37:27Z'
  action: captured
  score: 1.0
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_refs_baseline: 0.0
valid_from: '2026-10-03T16:37:27Z'
---

The `browser-check` gate of archify's `finalize` needs `ARCHIFY_CHROME` set to a Chrome/Chromium executable; without it the run skips the gate and exits non-zero even when validate/deliver/check all pass.

Why: this Mac has no system Chrome, Chromium, Edge, or Brave in /Applications. The only Chromium available is Playwright's cache, and its binary is named "Google Chrome for Testing" (not "Chromium"), so a naive find by that name returns nothing.

How to apply: run finalize with `ARCHIFY_CHROME="$HOME/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"`. If the Playwright cache version dir changes (chromium-NNNN), re-locate it under ~/Library/Caches/ms-playwright/.