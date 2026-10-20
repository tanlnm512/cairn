---
type: Knowledge-workflow
title: golden capture workflow
description: golden capture workflow
generated:
  by: cairn/0.22.0
  at: '2026-10-19T15:28:41Z'
okf_version: '0.2'
tier: asserted
doc_status: active
doc_owner: ''
doc_source: manual
epic_link: ''
affects_modules: []
affects_repos: []
steps:
- name: Record
  description: Run the golden recorder against the Python daemon.
  symbol: main
  file: scripts/rust_migration/golden_record.py
- name: Compare
  description: Diff each response against the fixtures.
  symbol: verify
  file: scripts/rust_migration/golden_record.py
---

Capture the MCP goldens, then diff every tool response.