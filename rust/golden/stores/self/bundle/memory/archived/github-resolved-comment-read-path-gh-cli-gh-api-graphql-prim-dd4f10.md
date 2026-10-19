---
type: Tribal-decision
title: 'GitHub resolved-comment read path: gh CLI (gh api graphql) primary, bearer-token
  curl fallback'
description: 'GitHub resolved-comment read path: gh CLI (gh api graphql) primary,
  bearer-token curl fallback'
resource: src/cairn/review/spans.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-17T03:11:09Z'
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
- date: '2026-09-17T03:11:09Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-09-17T03:11:09Z'
---

Read resolved review-comment state via GraphQL only: REST GET /repos/{owner}/{repo}/pulls/{n}/comments carries no isResolved field, no REST issue event fires on resolution, and gh pr view exposes no thread-resolution field; the only read path is repository.pullRequest.reviewThreads via POST /graphql. Authentication: gh CLI (gh api graphql, keyring-managed token) is the canonical fetch; curl -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/graphql is the documented fallback (GitHub Actions runners ship gh preinstalled and provide GITHUB_TOKEN). cairn itself never touches the network: cairn review --capture-event reads a JSON file produced by the surrounding automation. Event-file contract T005's adapter accepts, one thread per file: {"resolved": <bool>, "file": <str>, "line": <int|null>, "body": <str>, "thread_url": <str>}; provenance from the GraphQL thread node: resolved=isResolved, file=path, line=line (null degrades to file-level keying), body=comments.nodes[0].body (carries the cairn:pattern marker for type classification), thread_url=comments.nodes[0].url. One-command producer: gh api graphql -f query='<reviewThreads query>' --jq '.data.repository.pullRequest.reviewThreads.nodes[] | {resolved: .isResolved, file: .path, line: .line, body: .comments.nodes[0].body, thread_url: .comments.nodes[0].url}'.
Why: verified on scratch repo tanlnm512/cairn-spike-t006 PR 1 this session (resolved thread PRRT_kwDOUeOFzM6jMPq8 read via curl+token and gh api); repo has no GitHub HTTP client and stdlib-only HTTP elsewhere, so a client dependency would trip the C-03 dependency gate; gh api never shells to git, so it works even where gh's git subcommands break (this machine's /usr/local/bin/git is x86_64-only).
How to apply: T005 codes the adapter against the five-field shape above; T007's Action and any local hook fetch with the gh api graphql command; webhook trigger, when used, is pull_request_review_thread action=resolved.