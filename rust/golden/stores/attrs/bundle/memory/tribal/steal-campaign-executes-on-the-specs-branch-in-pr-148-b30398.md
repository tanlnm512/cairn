---
type: Tribal-decision
title: 'Steal campaign executes on the specs branch in PR #148'
description: 'Steal campaign executes on the specs branch in PR #148'
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-05T05:08:12Z'
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
- date: '2026-10-05T05:08:12Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-05T05:08:12Z'
---

All 8 steal-campaign specs are implemented ON the specs/steal-campaign-2026-10 branch inside PR #148 (user instruction "start implementing all specs in this PR", 2026-10-05), overriding the spec.md Branch fields (feat/<name>) and the merge-#148-first recommendation. Consequence: PR #148 carries docs + feat commits; each spec still gets its own C1 (implementation+ticks) and C2 (delivery record) commits and its own proof pass per the spec-to-prod execution mode; PR title updated to feat(...) once code lands.

**Why:** user chose single-PR delivery for the campaign over 8 stacked PRs.

**How to apply:** do not re-branch per spec on this campaign; review scope diffs per spec use the spec's own approval-freeze SHA as base, not main.