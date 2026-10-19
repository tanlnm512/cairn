---
type: Tribal-decision
title: worked-bundle scrub templates tempdir paths; repo anchor prefers cwd
description: worked-bundle scrub templates tempdir paths; repo anchor prefers cwd
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-05T14:24:24Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-05T14:24:24Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-05T14:24:24Z'
---

src/cairn/bench/worked.py privacy scrub (scrub_path) orders anchors: repo root (repo-relative), then Path.home() ($HOME/), then tempfile.gettempdir() ($TMPDIR/), else verbatim.

Why: tech-spec D-004 lists only repo-relative/$HOME, but its own NFR-002 test requires "neither str(Path.home()) nor the temp workspace's absolute path" in manifest.json — bench --workspace and the auto-generated corpus live under the system tempdir, so a verbatim pass could never satisfy that test. NFR-002's actual wording ("repo-relative or $VAR-templated paths only") licenses the $TMPDIR anchor.

How to apply: the inside-repo check for inventory appends resolves the repo anchor as first of (cwd, package tree) containing a benchmarks/ directory — cwd-first mirrors default_baselines_root()'s precedence and is what lets CliRunner tests exercise appends against a tmp repo. Scrub must compare resolved paths on both sides (macOS /var vs /private/var symlink).