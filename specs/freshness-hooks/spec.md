# Spec: freshness-hooks

**Status**: active
**Effort**: standard
**Created**: 2026-10-04
**Branch**: `feat/freshness-hooks`

## What
Cairn's git hooks keep the store fresh without the agent remembering to run
`cairn update`: the post-commit hook learns to diff the commit range
(HEAD@{1}..HEAD) instead of the worktree (which is empty right after a
commit), a post-checkout hook updates on branch switches, `cairn update`
gains a `--diff-ref A..B` scoping option, and `cairn init --with-hooks`
offers installation at init time.

## Why
The existing post-commit hook has a blind spot: it runs `cairn update`,
whose changed-file detection diffs the worktree against HEAD — but at
post-commit time the worktree matches HEAD, so the just-committed files are
never reindexed. Branch switches leave the store equally stale. Freshness
today depends on the agent triggering updates; hooks make it structural.

## Business value
Store staleness after commits and checkouts drops to zero without any agent
discipline; the update stays incremental (only changed files reparse) and
safe alongside a read-only server (WAL + busy-timeout already handle
concurrency). Success: after any commit or branch switch, a subsequent
`explore` reflects the new code without a manual update.

## User stories
### US1 — Commit freshness (P1)
As a developer using the git hooks, I want the store updated right after
every commit, so that the graph reflects committed code.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given the post-commit hook installed, When a commit adds/edits source
  files, Then `cairn update --diff-ref HEAD@{1}..HEAD` reindexes exactly
  those files.
- AC2: Given the hook runs, When it fires, Then the git operation is never
  blocked or failed by the update (background, errors swallowed).

### US2 — Checkout freshness (P2)
As a developer switching branches, I want the store updated to the new
branch, so that queries match what's on disk.

**Acceptance criteria**:
- AC1: Given the post-checkout hook installed, When a branch checkout
  completes, Then `cairn update --diff-ref <old>..<new>` runs in the
  background.
- AC2: Given a file checkout (`git checkout -- <path>`), When the hook
  fires, Then no update runs.

### US3 — Opt-in install at init (P3)
As a user running `cairn init`, I want a flag that installs the hooks, so
that setup is one command.

**Acceptance criteria**:
- AC1: Given a git-repo workspace, When `cairn init --with-hooks` runs,
  Then both hooks are installed idempotently with the same
  refuse-to-clobber rules as `cairn hooks install`.

## Requirements
- **FR-001**: `cairn update` shall accept `--diff-ref A..B` scoping
  changed-file detection to the git ref range, reusing the existing git
  plumbing; WHEN the flag is absent, the system shall keep today's
  worktree+untracked detection unchanged.
- **FR-002**: The post-commit hook template shall invoke
  `cairn update --diff-ref HEAD@{1}..HEAD` in the background with output
  discarded, replacing the current worktree-diff invocation.
- **FR-003**: The system shall support a post-checkout hook template: WHEN
  the checkout is a branch switch (exit code 1), the system shall run
  `cairn update --diff-ref $1..$2` in the background; WHEN it is a file
  checkout (exit code 0), the system shall do nothing.
- **FR-004**: `cairn hooks install` and `cairn hooks uninstall` shall manage
  both hooks idempotently, preserving the refuse-to-clobber guard and the
  cairn-marker removal rule.
- **FR-005**: `cairn init` shall accept `--with-hooks`, installing both
  hooks with the same rules as `cairn hooks install` (opt-in, never
  interactive).
- **FR-006**: WHERE the workspace is not a git repository, hook installation
  shall fail cleanly with guidance instead of writing partial hooks.

## Quality attributes
- **NFR-001**: Security — applicable: hook script content shall remain
  injection-safe (repo names validated before template interpolation).
- **NFR-002**: Privacy — not applicable: no new data leaves the store.
- **NFR-003**: Performance — applicable: WHEN either hook fires, the system
  shall add negligible foreground overhead to the git operation (update
  runs detached in the background).
- **NFR-004**: Reliability — applicable: WHEN the background update fails
  (lock contention, missing store), the git operation shall still succeed
  (best-effort hook, errors swallowed).
- **NFR-005**: Observability — not applicable: hook output is discarded by
  design; `cairn serve status`/`cairn doctor` remain the health surfaces.
- **NFR-006**: Accessibility — not applicable: no UI surface is touched.

## Scope
**In**: `cairn update --diff-ref`; post-commit template fix; post-checkout
template; hooks install/uninstall coverage for both hooks; `cairn init
--with-hooks`; non-git clean failure; docs.
**Out (deferred)**: watch mode (`FileWatcherService` already exists as a
separate surface); a graph-file merge driver (meaningless for the central
SQLite store); post-merge/post-rebase hooks; Windows hook parity; changing
`install-agents` defaults (the `--git-hooks` flag rides the new templates
automatically).

## Assumptions & risks
- Assumption: `HEAD@{1}` resolves correctly immediately after commit in
  worktrees without prior reflog entries on the branch (verify on a fresh
  repo; fall back to `ORIG_HEAD`-style capture if not — decide in
  tech-spec with evidence).
- Risk: background updates racing a commit sequence could reindex a stale
  range — mitigation: the update's existing build lock serializes writers;
  the range form recomputes nothing outside its ref span.
- Risk: users with pre-existing non-cairn post-commit hooks must not be
  clobbered — mitigation: the refuse-to-clobber guard is pinned behavior
  and is extended, not changed.
