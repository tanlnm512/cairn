# PR Review Checklist

> The review/audit gate for every change (feature, improvement, bugfix). Run by
> the author before requesting review, and by the reviewer before approving.
> Uses cairn's own tools to turn "does it look right?" into "what's the blast
> radius, and did we update everything that depends on this change?"

**TL;DR — every PR must answer four questions:**
1. **Blast radius** — what else breaks if this change is wrong? (`explore` + `impact_analysis`)
2. **Layering** — does this respect the documented architecture? (`ask_compass`)
3. **Post-task hygiene** — did `cairn update` + `record_memory` run? (AGENTS.md §"After completing a task")
4. **Coverage** — is the change tested, and is the CHANGELOG updated?

This is Layer 2 of the review pipeline. Layers 0-1 (pre-commit + CI) are
automated; this is the human/agent layer that catches what they can't: intent,
design, and second-order blast radius.

---

## 1. Get the change surface

```bash
# Files touched by this PR vs the base branch
git diff --name-only main...HEAD
```

## 2. Blast radius (the audit core)

For every public/API symbol you changed (renamed, re-typed, moved, deleted):

- `explore("<symbol>")` — matching source + call paths + depth-2 blast radius.
- If the signature changed or it's widely used: `impact_analysis("<symbol>")`
  (recursive callers). Use `fuzzy=True` if the precise result looks suspiciously
  small for a common name (see AGENTS.md §"Resolution-aware querying").
- If it's part of cairn's public API: `cross_repo_deps("cairn")` — downstream
  consumers that may break.

> A change with no *resolvable* callers might still be used. Empty precise ≠
> unused — re-run with `fuzzy=True` before concluding a symbol is safe to touch.

## 3. Layering & past decisions

For each changed file:

- `ask_compass(file_path="<path>")` — load the module's navigation guide + memory.
  Does the change respect the documented layers, or cross a boundary the compass
  says it shouldn't?
- If the area has tribal context: `recall_memory("<symbol>")` and
  `search_knowledge("<topic>", type_filter="Wiki")` — is there a past decision
  this change contradicts?

## 4. Post-task hygiene (verify AGENTS.md §"After completing a task")

These are the steps AGENTS.md requires after *every* task. The review confirms
they actually ran — not just that they were claimed:

- [ ] `cairn update` ran — the graph reflects the new code. A stale graph gives
      the *next* change a wrong blast radius.
- [ ] `record_memory` called for any new decision / pattern / mistake / workaround.
- [ ] **Doctor on fallback/perf changes** — if the change altered a fallback or
      performance path (ANN→brute-force, hash-embed, lock-contention swallow,
      result truncation, semantic-backend switch), `cairn doctor` was run and
      acted on, and the telemetry signal exposing any degradation is named
      (spec §6.4 event catalog). A silent degrade is the bug class this codebase
      is built to avoid.
- [ ] Tests are hermetic — no dependence on the dev machine's PATH/HOME/env
      (the suite-wide `_hermetic_env` fixture is the default; use
      `@pytest.mark.real_env` only with justification); parse CLI output via
      `result.stdout`, never `result.output` (interleaves stderr)
- [ ] Tests added or updated to cover the change (`pytest -m core` for the fast
      loop, then the full suite).

## 5. Change-type checks

- **Feature / improvement** — CHANGELOG `[Unreleased]` entry; a new navigable
  tool/symbol gets a compass or wiki entry; document any new public API that
  nothing resolves yet (so it isn't mistaken for dead code).
- **Bugfix** — a regression test that fails before the fix and passes after;
  note the root cause, and a `record_memory` mistake entry if it's a trap others
  could hit.
- **Refactor** — behavior unchanged: the full test suite is green and
  `impact_analysis` shows no *new* unresolved callers. (`make
  verify-no-code-change` is for comment-only edits, not refactors — code is
  *expected* to change here.)

## 6. Pre-review gates (already automated — should be green)

Enforced by Layers 0-1; if any is red, fix before requesting review:

- pre-commit: `ruff`, `gitleaks`, yaml/toml/merge-conflict/large-file checks
  (`.pre-commit-config.yaml`)
- pip-audit (hard gate) + conventional PR title (hard gate)
- bandit / mypy (advisory — review any *new* findings they surface)

---

## Quick reference: the one-line review

`explore` the changed symbols → `impact_analysis` for breakage → `ask_compass`
for layering → verify `cairn update` + `record_memory` ran → tests + CHANGELOG.
