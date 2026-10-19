---
type: Tribal-decision
title: 'PR #148 merged 2e7a7e7 after two CI fix rounds'
description: 'PR #148 merged 2e7a7e7 after two CI fix rounds'
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-05T15:09:31Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-05T15:09:31Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-10-05T15:09:31Z'
---

PR #148 (steal campaign: 8 specs authored+implemented) MERGED to main at 2e7a7e7 on 2026-10-05 via `gh pr merge --admin` (branch protection requires an approval the PR author cannot give; --admin is the established self-merge mechanism). Branch deleted. CI was NOT green on first merge attempt: container smoke hit the boot DB-guard before the D-004 key refusal (fix: cairn init --no-build first in the run command + --entrypoint /bin/sh to override the image ENTRYPOINT — plain `sh -c` after the image appends args TO cairn), and two import-scip tests failed on fresh CI checkouts because the scip fixture workspaces' .git markers are uncommittable (fix: autouse fixture mkdir's them — scanner discovers repos by the marker). mypy also caught 5 real typing issues (mixin attr declarations, object-dict sort key, info_name/peer_id narrows).

**Why:** "merge if all green" required two fix commits (dfa0d1c mypy + init-first, 11e672b markers + entrypoint) before the rollup went green.

**How to apply:** fixture workspaces needing a .git marker must mkdir it in-test (fresh checkouts never have it); `docker run IMAGE sh -c` does NOT override ENTRYPOINT; always verify `gh pr checks` against the current head SHA before merging.