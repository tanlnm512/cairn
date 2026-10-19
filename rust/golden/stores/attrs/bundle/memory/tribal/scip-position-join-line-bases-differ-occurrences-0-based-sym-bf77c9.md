---
type: Tribal-pattern
title: 'SCIP position join: line bases differ (occurrences 0-based, symbols table
  1-based); fixture .git provisioning hazards'
description: 'SCIP position join: line bases differ (occurrences 0-based, symbols
  table 1-based); fixture .git provisioning hazards'
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-20T17:52:50Z'
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
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-20T17:52:50Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-20T17:52:50Z'
---

The SCIP importer's position join converts bases: scip.Occurrence lines are 0-based (both the deprecated repeated range and typed single/multi_line_range), while cairn's symbols.line_start/line_end are 1-based (tree-sitter start_point[0]+1) — containment test is `line_start <= occ_start+1 and occ_end+1 <= line_end`, columns stay 0-based on both sides. Fixture family tests/fixtures/scip-indexing needs a .git marker per workspace or scanner.discover_repos finds 0 repos (bare empty .git dir suffices, provision.sh creates real repos + initial commit). How to apply: when importing/creating indexes or provisioning fixture repos, never compare occurrence lines to symbols lines without the +1; commit fixture SOURCES to the outer repo before provision.sh first runs — directory-form `git add` of a tree containing nested repos records mode-160000 gitlinks that clone empty.