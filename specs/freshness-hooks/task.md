# Tasks: freshness-hooks

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: pending — delivery evidence; the orchestrator writes `commit @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 1 | 1 |
| 2 | 3 | 3 |
| 3 | 3 | 3 |
| **Σ** | 7 | 7 |

## Phase 1: Range-scoped update (FR-001)
<!-- Checkpoint: `cairn update --help` shows --diff-ref; a two-commit scratch repo reindexes exactly the second commit's files with `--diff-ref 'HEAD@{1}..HEAD'`; flag-less update unchanged (survey S2). -->
- [x] T001 (implemented) Add `--diff-ref A..B` range scoping to `cairn update` — CLI option with endpoint pre-validation plus optional `diff_ref` keyword through `incremental_update` into `_changed_source_files`' range mode, failing-test-first (FR-001)
  - done 2026-10-05 — done 2026-10-05 — range-mode tests + live e2e (HEAD~1..HEAD reindexes committed files, clean worktree); 79 passed incremental+audit suites
  - Touches:
    - `src/cairn/cli/update.py`
    - `src/cairn/graph/incremental.py`
    - `tests/test_incremental_derived.py`
  - Consumes/produces: new optional kwargs `incremental_update(..., diff_ref: str | None = None)` and `_changed_source_files(repo_path, conn=None, diff_ref=None)` — range mode runs `git diff --name-only <range>` only (untracked + stat fallback skipped, D-004); flag absent = today's behavior; endpoints verified via `git rev-parse -q --verify`, clean CLI error otherwise (D-002).

## Phase 2: Hooks keep the store fresh (FR-002, FR-003, FR-004)
<!-- Checkpoint: commit reindexes the committed files; branch switch reindexes old..new; file checkout no-ops; install/uninstall cover both hooks with guard pins green (survey S1, S3, S4). -->
- [x] T002 (implemented) Rewrite the post-commit template to a background `--diff-ref` range invocation with the first-commit empty-tree fallback (after T001) (FR-002, NFR-001, NFR-004)
  - done 2026-10-05 — done 2026-10-05 — ranged template + empty-tree fallback; live first-commit arm fired computed empty-tree hash; fidelity pins kept contiguous
  - Touches:
    - `src/cairn/hooks/git_hooks.py`
    - `tests/test_install_uninstall_fidelity.py`
  - Consumes from T001: the `--diff-ref A..B` option on `cairn update` (single range string → `git diff --name-only`). Template resolves `git rev-parse -q --verify 'HEAD@{1}'`, else `$(git hash-object -t tree /dev/null)..HEAD` (D-001); keeps pinned substrings `cairn update --repo "{repo}"` and `cairn validate-paths --mark`, output discarded, `&` backgrounded; default-home render stays byte-identical to the constant.
- [x] T003 (implemented) Add the post-checkout hook template — branch switch backgrounds `--diff-ref $1..$2`, file checkout no-ops (after T002) (FR-003, NFR-003)
  - done 2026-10-05 — done 2026-10-05 — post-checkout guard [ $3 = 1 ]; live: branch switch fires old..new, file checkout spawns zero cairn processes
  - Touches:
    - `src/cairn/hooks/git_hooks.py`
    - `tests/test_install_uninstall_fidelity.py`
  - Consumes from T002: the module's template/render/install structure (`POST_CHECKOUT_TEMPLATE` alongside the rewritten `POST_COMMIT_TEMPLATE`, same `_render_*` export-line convention). Guard is `[ "$3" = "1" ]` — `$3` is git's branch-checkout flag (the spec's "exit code 1"); `$1..$2` are git-provided SHAs, no reflog resolution.
- [x] T004 (implemented) Generalize `install_hooks`/`uninstall_hooks` to manage both hook names per repo, preserving refuse-to-clobber and cairn-marker rules (after T003) (FR-004)
  - done 2026-10-05 — done 2026-10-05 — both hooks per repo, clobber/marker rules per file; foreign-hook + uninstall live runs green; 145 passed fidelity+graft
  - Touches:
    - `src/cairn/hooks/git_hooks.py`
    - `tests/test_install_uninstall_fidelity.py`
    - `tests/test_graft_parity_repos.py`
  - Consumes from T003: both templates exist in `src/cairn/hooks/git_hooks.py`. Signatures and return contracts unchanged (list of repo names); per-hook-file guard/rewrite/uninstall; repo-name validation stays up-front before any write (D-003); existing installs upgraded by in-place rewrite.

## Phase 3: One-command opt-in setup (FR-005, FR-006)
<!-- Checkpoint: `cairn init --with-hooks` installs both hooks idempotently in a git workspace; non-git workspace gets guidance and zero partial hooks; docs updated. -->
- [x] T005 (implemented) Add `--with-hooks` to `cairn init`, wiring `install_hooks` after store creation, opt-in and non-interactive (after T004) (FR-005)
  - done 2026-10-05 — done 2026-10-05 — init --with-hooks idempotent (md5-identical reinstall); 80 passed fidelity+smoke
  - Touches:
    - `src/cairn/cli/core.py`
    - `tests/test_install_uninstall_fidelity.py`
  - Consumes from T004: generalized `install_hooks(repos, workspace)` managing both hooks, returning installed repo names. Flag installs with the same rules as `cairn hooks install`; never prompts.
- [x] T006 (implemented) Add non-git clean-failure guidance and both-hooks surface text (after T005) (FR-006)
  - done 2026-10-05 — done 2026-10-05 — non-git: hooks install exit 1 + guidance, init warns exit 0; zero partial hooks pinned
  - Touches:
    - `src/cairn/cli/hooks_viz.py`
    - `src/cairn/cli/core.py`
    - `tests/test_install_uninstall_fidelity.py`
  - Consumes from T005: the init-side hook-install call site in `src/cairn/cli/core.py`. Zero discovered repos: `cairn hooks install` prints guidance and exits 1; `init --with-hooks` warns and init still succeeds — no partial hooks either way (D-005); success text covers post-commit + post-checkout (survey S5 gap).
- [x] T007 (implemented) [P] Update docs for the new flag, both hooks, and `init --with-hooks` (FR-002, FR-003, FR-005)
  - done 2026-10-05 — done 2026-10-05 — cli-reference + CHANGELOG; doc-links 0 broken
  - Touches:
    - `docs/configuration.md`
    - `docs/cli-reference.md`

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `(implemented)` landed —
      implementation evidence durable before the one all-at-once tick ·
      `- [x]` done + proof note: `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder. From round 2 on, an
  implementer's scratch note (what was tried, why it failed) may live at
  `notes/T###.md` — the one file an implementer may write under specs/,
  never read by check.py, never counted as status
- Every task cites FR-### or an applicable NFR-###; a task with neither is
  scope creep — fix the spec first
- Every code task carries a `Touches:` block (repo-relative files,
  directories, or globs); `[P]` tasks whose touches overlap are chained, not
  spawned together
