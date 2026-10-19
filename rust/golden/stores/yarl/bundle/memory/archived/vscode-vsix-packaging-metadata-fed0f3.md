---
type: Tribal-pattern
title: vscode vsix packaging metadata
description: vscode vsix packaging metadata
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T14:40:50Z'
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
memory_tier: archived
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T14:40:50Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-09-17T14:40:50Z'
---

extensions/vscode packages via npm devDep @vscode/vsce (npm run package -> vsce package -o cairn-ext.vsix). Non-interactive vsce package requires: publisher field, repository field, README.md, LICENSE (MIT copy) in the extension root; missing any of these triggers a y/N prompt that aborts a non-TTY build. .vscodeignore excludes src/, test/, out/test/, tsconfig.json, .gitignore, package-lock.json; compiled out/src is the shipped code. icon.png is a generated 128x128 RGBA PNG. Local machine has no VS Code app/CLI, so code --install-extension verification is deferred to the clean-machine step (T009).