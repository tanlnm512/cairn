<!--
Review procedure: docs/review-checklist.md (blast radius via cairn tools +
post-task hygiene verification). Fill the checklist below; the reviewer
confirms each box. Layers 0-1 (pre-commit + CI) are automated -- this template
is the human/agent audit layer.
-->

## Summary

<!-- 1-3 lines: what & why. Link the issue if any. -->

## Change type

- [ ] Feature / improvement
- [ ] Bugfix
- [ ] Refactor (no behavior change)
- [ ] Docs / chore / CI

## Audit checklist

Author — confirm before requesting review (procedure: `docs/review-checklist.md`):

- [ ] **Blast radius checked** — `explore` + `impact_analysis` on changed public symbols; `cross_repo_deps("cairn")` if public API touched
- [ ] **Layering respected** — `ask_compass` on changed files; no documented-boundary violations
- [ ] **Graph updated** — `cairn update` ran so blast-radius is current
- [ ] **Memory recorded** — `record_memory` for any decision / pattern / mistake
- [ ] **Fallback/perf paths** — if the change alters a fallback or performance path, `cairn doctor` was run and the telemetry signal exposing the degradation is named (spec §6.4)
- [ ] **Tests** — change is covered (`pytest -m core` fast loop + full suite)
- [ ] **CHANGELOG** — `[Unreleased]` entry added (feature/bugfix only)
- [ ] **Gates green** — pre-commit, pip-audit, conventional PR title; reviewed any new mypy/bandit findings

## Blast-radius note

<!-- For anything signature-breaking or widely-used: name the callers/consumers
confirmed safe, or "none — new symbol". -->

## Verification

<!-- How you verified: commands run, manual checks, test results. -->
