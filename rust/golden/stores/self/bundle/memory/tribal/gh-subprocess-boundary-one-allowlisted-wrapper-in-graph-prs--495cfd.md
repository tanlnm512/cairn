---
type: Tribal-decision
title: 'gh subprocess boundary: one allowlisted wrapper in graph/prs.py'
description: 'gh subprocess boundary: one allowlisted wrapper in graph/prs.py'
resource: src/cairn/graph/prs.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-05T13:02:36Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.502
memory_signals:
  graph_verification: 0.667
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
- date: '2026-10-05T13:02:36Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-05T13:02:36Z'
---

src/cairn/graph/prs.py is the single gh boundary: `_gh(repo, args)` runs `subprocess.run(["gh", *args], cwd=repo, capture_output=True, text=True, errors="replace", timeout=_GH_TIMEOUT)` and accepts ONLY argv `["--version"]` or `["pr", list|view|diff, ...]` — anything else raises GhError before exec. All failures (non-zero exit, FileNotFoundError, TimeoutExpired) raise `GhError` whose message carries gh's stderr or install/auth guidance.

Why: FR-002/NFR-001/NFR-004 of specs/pr-tooling demand clean failures, subprocess-only invocation with fixed argv, and gh's own messages; D-001/D-002 pin this shape.

How to apply: never call subprocess with "gh" outside this module; add new gh surfaces by extending `_gh`'s allowlist only. gh JSON is untrusted input — parse via parse_pr_list/parse_pr_view (required fields fail closed naming `gh --version`, unknown fields ignored, author coerced from {"login"} or str, statusCheckRollup classified conservatively pass/fail/pending/never-pass-on-unrecognized). Tests shadow gh on PATH with a stub script (C-04: never patch global subprocess.Popen); `_GH_TIMEOUT` is monkeypatchable for timeout tests.