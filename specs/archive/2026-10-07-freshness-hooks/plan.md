# Plan: freshness-hooks

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1 | Range-scoped update | `cairn update --diff-ref A..B` reindexes exactly the files in the git ref range; flag absent keeps today's worktree+untracked detection | FR-001 | — |
| 2 | Hooks keep the store fresh | A commit reindexes the commit's files via the range form (first-commit fallback per tech-spec D-001); a branch switch reindexes old..new; a file checkout does nothing; install/uninstall manage both hooks with the guard intact | FR-002, FR-003, FR-004, NFR-001, NFR-003, NFR-004 | Phase 1 |
| 3 | One-command opt-in setup | `cairn init --with-hooks` installs both hooks; non-git workspaces get clean guidance, never partial hooks; docs updated | FR-005, FR-006 | Phase 2 |

## Dependencies
Phase 1 is the foundation: the hook templates (Phase 2) invoke the
`--diff-ref` form, so nothing in Phase 2 can be demoed before it exists.
Phase 3's `init --with-hooks` calls the generalized `install_hooks`
(Phase 2) and inherits both templates. The spine is strictly ordered:
Phase 1 → Phase 2 → Phase 3. Nothing in this spec has two independent
foundations — every FR lands on the same update path.

## Parallelization map
- Strictly ordered: Phase 1 (`--diff-ref` in `src/cairn/cli/update.py` + `src/cairn/graph/incremental.py`) → Phase 2 (`src/cairn/hooks/git_hooks.py` templates + install/uninstall) — Phase 2's checkpoint runs the flag Phase 1 produces; interface consumed: CLI option `--diff-ref A..B` (single range string, passed through to `git diff --name-only`), verified by `cairn update --help`.
- Strictly ordered (within Phase 2): post-commit template → post-checkout template → install/uninstall generalization — all three rewrite `src/cairn/hooks/git_hooks.py`; shared file, serial chain.
- Strictly ordered (within Phase 3): `init --with-hooks` → non-git guidance — both touch `src/cairn/cli/core.py`'s init path.
- Independent: docs (`docs/configuration.md`, `docs/cli-reference.md`) ∥ everything in Phase 3 — disjoint files, no shared state; can run parallel to the init work.
- Solo context: one implementer works the spine; the `[P]` markers exist so a second implementer (or a later resume) can take the docs task without blocking.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: `cairn update --help` shows `--diff-ref`; in a two-commit scratch repo, `cairn update --repo <r> --diff-ref 'HEAD@{1}..HEAD'` reindexes exactly the second commit's files; flag-less update is unchanged — `python3 -m pytest tests/test_workflow_audit_fixes.py tests/test_incremental_derived.py -q` green.
- **After Phase 2**: in a scratch workspace with hooks installed, a commit reindexes the committed files (explore reflects the new code without manual update); a branch switch reindexes old..new; `git checkout -- <file>` runs no update; `python3 -m pytest tests/test_install_uninstall_fidelity.py tests/test_graft_parity_repos.py -q` green (guard + marker pins intact).
- **After Phase 3**: `cairn init --with-hooks` in a git workspace installs both hooks idempotently; in a non-git directory it prints guidance and writes no hooks; `cairn hooks install` outside git exits cleanly with guidance; docs pages mention the new flag/flag-init.

## Risks & mitigations
- Risk: `HEAD@{1}` fails on the first commit of a fresh repo (verified this session: `git rev-parse HEAD@{1}` exits 128 when the HEAD reflog has one entry) → mitigation: template-local fallback to an empty-tree range (tech-spec D-001), keeping the CLI literal.
- Risk: background updates racing a commit sequence reindex a stale range → mitigation: the update path already serializes writers via the build lock and 20s busy timeout (survey S8, status DONE); the range form recomputes nothing outside its ref span.
- Risk: pre-existing non-cairn hooks get clobbered by the second hook name → mitigation: refuse-to-clobber and cairn-marker removal are pinned behavior (survey S4/S9) extended per hook name, never changed.

## Delivery
Solo, PR-per-milestone: one branch (`feat/freshness-hooks`) cut per Phase, one PR per Phase with conventional-commit tasks landed in order, each PR watched through CI per `specs/CONSTITUTION.md` C-01. Phase 1's PR is the de-risk gate — if the range form misbehaves, Phases 2-3 haven't started.
