# Spec: pr-review-loop

**Status**: draft
**Created**: 2026-09-16
**Branch**: `feat/pr-review-loop`

## What
A closed review loop built on the existing blast-radius engine:
`cairn review --base <ref>` produces a structurally-grounded review-context
pack for a diff, `cairn review --pre-submit` checks a diff against
accumulated behavioral memories before a PR opens, and an auto-capture hook
records accepted review comments as symbol-keyed memories so the next PR
benefits.

## Why
Review comment → persistent behavioral rule is the proven self-improving
agent pattern. Cairn's memory system (decisions, patterns, mistakes,
workarounds — symbol-keyed with tier lifecycles and decay) is the substrate,
and `cairn blast` already does the hardest half of review context: it seeds
symbols from a git diff against a base ref and computes the precise reverse-
dependency radius (fuzzy opt-in), refreshing drifted files first when asked.
Two more existing pieces close the loop cheaply: stored symbol spans
(`symbols.line_start`/`line_end`) map a review comment's file+line to the
enclosing symbol, and the memory store already has an auto-capture
precedent — the recurrence-gated failure-signature capture
(`memory_failure_signatures`) that decides what is worth recording without
human steps. Nothing today connects PR outcomes back to memory writes.

## Business value
Agents stop repeating review-flagged mistakes: accepted comments become
persistent rules; pre-submit guards surface them before review. Success:
a resolved review comment auto-records as memory keyed to affected symbols,
and the next diff touching those symbols triggers the guard warning.

## User stories
### US1 — Review context pack (P1)
As a reviewing agent, I want the blast radius, relevant memories, compass,
and wiki sections for a diff in one structured pack.

**Acceptance criteria** (each trace to an FR below):
- AC1: Given a diff, When `cairn review --base main` runs, Then the output
  contains the precise blast radius plus memories/compass/wiki relevant to
  the affected symbols (FR-001).
- AC2: Given an empty radius, When review runs, Then it exits 0 with a
  no-dependents statement (FR-001).

### US2 — Pre-submit guard (P1)
As an agent about to open a PR, I want warnings when my diff touches symbols
with recorded mistakes/patterns.

**Acceptance criteria**:
- AC1: Given memories of type mistake/pattern keyed to symbols in the diff,
  When `cairn review --pre-submit` runs, Then each relevant memory surfaces
  as a warning with its recorded guidance (FR-002).

### US3 — Auto-capture (P1)
As a maintainer, I want accepted review comments recorded as memory without
manual steps.

**Acceptance criteria**:
- AC1: Given a resolved (accepted) review comment tied to changed symbols,
  When the capture hook runs, Then a memory of the appropriate type is
  recorded, keyed to those symbols, linking the PR thread (FR-003).

## Requirements
- **FR-001**: The system shall provide `cairn review --base <ref>` that
  computes the diff blast radius via the existing blast engine and enriches
  it with memories, compass entries, and wiki sections matching the affected
  symbols, emitting a structured review-context pack (text and markdown).
- **FR-002**: `cairn review --pre-submit` shall check a diff against memories
  of type mistake and pattern keyed to affected symbols and surface each
  match as a warning; exit code shall be non-zero only when configured to
  gate.
- **FR-003**: The system shall provide an auto-capture hook that, WHERE a
  review comment is resolved/accepted against changed symbols, records it as
  a memory (mistake or pattern), keyed to those symbols, with a link to the
  PR thread.
- **FR-004**: The system shall ship a GitHub Action invoking
  `cairn review --pre-submit` on pull-request events and posting findings as
  PR comments.
- **FR-005**: The review loop shall reuse existing surfaces (blast engine,
  memory store API, compass/wiki readers) without duplicating graph or
  memory logic.

## Scope
**In**: review command (context pack + pre-submit), auto-capture hook,
GitHub Action, tests with fixture repos and memories.
**Out (deferred)**: GitLab/GitLab-CI adapters; LLM-generated review comments
(cairn supplies context, not judgments); memory editing UX in the review
flow; multi-PR batch capture.

## Assumptions & risks
- Assumption: GitHub is the first review platform; the capture hook reads
  resolved-review-comment events via GitHub API/Actions.
- Risk: comment-to-symbol attribution may be imprecise when comments target
  files rather than symbols — mitigation: map comment file+line to enclosing
  symbol via stored spans, degrade to file-level memory when no symbol
  encloses.
- Risk: memory volume growth from auto-capture — mitigation: captured
  memories enter as drafts (existing tier lifecycle) subject to decay.
