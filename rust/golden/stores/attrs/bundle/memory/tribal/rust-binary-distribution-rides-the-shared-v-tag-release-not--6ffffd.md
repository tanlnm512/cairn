---
type: Tribal-decision
title: Rust binary distribution rides the shared v-tag release, not cargo-dist
description: Rust binary distribution rides the shared v-tag release, not cargo-dist
resource: .github/workflows/release-rust.yml
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-19T09:15:04Z'
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
- date: '2026-10-19T09:15:04Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

Workstream 3c ships the Rust cairn binary via hand-rolled .github/workflows/release-rust.yml (aarch64/x86_64 macOS + x86_64/aarch64 musl-static Linux), attaching tarballs+sha256 to the GitHub Release that release.yml owns, plus a templated Homebrew tap formula (packaging/homebrew/cairn.rb.template, rendered by scripts/render_homebrew_formula.sh, pushed when vars.HOMEBREW_TAP_REPO/secrets.HOMEBREW_TAP_TOKEN are set) and [package.metadata.binstall] in cairn-cli. Why: cargo-dist generates a fixed .github/workflows/release.yml that would collide with the Python release workflow (untouchable pre-flip) and wants to own the Release object; the extension-release.yml attach-to-shared-release precedent solves both. How to apply: keep distribution changes in release-rust.yml; adopt cargo-dist only as a spec amendment after the 3d flip. Homebrew audit quirk: explicit version is redundant when the URL embeds the version, so bake @VERSION@ into the URL and let brew infer it.