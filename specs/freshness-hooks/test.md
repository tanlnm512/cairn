# Test Cases: freshness-hooks

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details. Derived from
spec.md only — tech-spec.md and plan.md were never read (implementation
blindness is the point).

**Conventions**: pass-condition commands are self-contained one-liners run
from the repo root (scratch git repo and scratch cairn home under a temp
dir, seconds to run, well under the 120 s per-TC cap; nothing is written
under the repo working tree). Each scaffold:
- prepends a per-run `cairn` shim dir to PATH — a script that execs this
  checkout's CLI via `uv run --no-sync --project`, so both the typed
  commands and the fired hooks exercise THIS checkout (a globally
  installed `cairn` may predate the feature); the shim also logs each
  invocation to the temp dir, giving hook-fire observability;
- pins `CAIRN_HOME` into the temp dir, so the store, the workspace
  registry, and the hook's rendered `export CAIRN_HOME` line all stay
  under the temp dir;
- sets a local git identity so commits never prompt.

`cairn def <symbol> --no-refresh` is the observable store-truth query
surface: exit 0 with a definition line when indexed, exit 1 with "No
definition found" when not. `--no-refresh` is load-bearing: the default
query reindexes drifted worktree files before answering, which would let
a lazy query fake both freshness (TC-001, TC-005) and absence (TC-002,
TC-006) — with it, a pass can only come from the hook-fired update.
Hook-fired updates run in the background, so freshness TCs poll within a
bounded grace period before the final query.

## TC-001 — Commit freshness: a commit's changes are queryable right after it lands
- **Story**: US1 · **Traces to**: FR-002, AC1
- **Given** a git workspace with cairn initialized and hooks installed, and the store synced to a committed base file
- **When** the developer edits that file and commits (post-commit hook fires)
- **Then** a subsequent query reflects the committed change with no manual update
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC001.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && git config user.email t@t && git config user.name t && printf "def base_fn():\n    pass\n" > a.py && git add a.py && git commit -qm c0 >/dev/null && cairn init --with-hooks >/dev/null 2>&1 && printf "def committed_fn():\n    pass\n" >> a.py && git commit -aqm c1 >/dev/null && for i in 1 2 3 4 5 6 7 8 9 10; do cairn def --no-refresh committed_fn >/dev/null 2>&1 && break; sleep 1; done && cairn def --no-refresh committed_fn >/dev/null 2>&1 && echo TC001_PASS'`

## TC-002 — Range scoping: update touches exactly the files in the ref range
- **Story**: US1 · **Traces to**: FR-001, FR-002, AC1
- **Given** a synced store where the last commit changed one file and an unrelated untracked scratch file exists in the worktree
- **When** the user runs an update scoped to the last commit's range
- **Then** the committed change is indexed, the out-of-range scratch file is not, and prior data survives
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC002.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && git config user.email t@t && git config user.name t && printf "def base_fn():\n    pass\n" > a.py && git add a.py && git commit -qm c0 >/dev/null && cairn init >/dev/null 2>&1 && printf "def committed_fn():\n    pass\n" >> a.py && git commit -aqm c1 >/dev/null && printf "def scratch_fn():\n    pass\n" > u.py && cairn update --diff-ref "HEAD@{1}..HEAD" >/dev/null 2>&1 && cairn def --no-refresh committed_fn >/dev/null 2>&1 && ! cairn def --no-refresh scratch_fn >/dev/null 2>&1 && cairn def --no-refresh base_fn >/dev/null 2>&1 && echo TC002_PASS'`

## TC-003 — Default detection unchanged: update without the flag still sees the worktree (standing guard)
- **Story**: US1 · **Traces to**: FR-001
- **Given** a synced store and an uncommitted edit to a tracked file
- **When** the user runs a plain update (no flag)
- **Then** the worktree edit is indexed exactly as today; the new flag must not change flag-less behavior
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC003.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && git config user.email t@t && git config user.name t && printf "def base_fn():\n    pass\n" > a.py && git add a.py && git commit -qm c0 >/dev/null && cairn init >/dev/null 2>&1 && printf "def edited_fn():\n    pass\n" >> a.py && cairn update >/dev/null 2>&1 && cairn def --no-refresh edited_fn >/dev/null 2>&1 && echo TC003_PASS'`

## TC-004 — Boundary: first commit ever on a brand-new repository
- **Story**: US1 · **Traces to**: FR-002, NFR-004, AC2
- **Given** a freshly created repository with no prior history and hooks installed (spec assumption: the previous-commit reference may not resolve here — how to resolve it is an implementation decision; the pinned business promise is non-breakage)
- **When** the very first commit lands
- **Then** the commit succeeds; if the range cannot be resolved, the failed update is swallowed and never surfaced to the user
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC004.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && git config user.email t@t && git config user.name t && cairn init --with-hooks >/dev/null 2>&1 && printf "def first_fn():\n    pass\n" > a.py && git add a.py && git commit -qm c0 >/dev/null 2>&1 && echo TC004_PASS'`

## TC-005 — Checkout freshness: switching branches updates the store
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** a synced store and a second branch whose commits change source files
- **When** the developer switches to that branch (branch-switch checkout)
- **Then** a subsequent query reflects the newly checked-out branch's code with no manual update
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC005.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && git config user.email t@t && git config user.name t && printf "def base_fn():\n    pass\n" > a.py && git add a.py && git commit -qm c0 >/dev/null && cairn init --with-hooks >/dev/null 2>&1 && git checkout -qb feature && printf "def feature_fn():\n    pass\n" >> a.py && git commit -aqm f1 >/dev/null && git checkout -q - && git checkout -q feature && for i in 1 2 3 4 5 6 7 8 9 10; do cairn def --no-refresh feature_fn >/dev/null 2>&1 && break; sleep 1; done && cairn def --no-refresh feature_fn >/dev/null 2>&1 && echo TC005_PASS'`

## TC-006 — Checkout freshness: a file checkout triggers no update
- **Story**: US2 · **Traces to**: FR-003, AC2
- **Given** a synced store with hooks installed; one tracked file carries an uncommitted worktree edit, and a second tracked file carries a separate disposable worktree edit
- **When** the developer restores the second file (`git checkout -- <path>` — a file checkout, not a branch switch)
- **Then** no update runs: the unrelated worktree edit stays out of the store, and the store keeps its prior content
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC006.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && git config user.email t@t && git config user.name t && printf "def base_fn():\n    pass\n" > a.py && printf "def bbase_fn():\n    pass\n" > b.py && git add a.py b.py && git commit -qm c0 >/dev/null && cairn init --with-hooks >/dev/null 2>&1 && printf "def dirty_fn():\n    pass\n" >> b.py && printf "def scrap_fn():\n    pass\n" >> a.py && git checkout -q -- a.py && sleep 3 && ! grep -q "^update" "$T/log.txt" && ! cairn def --no-refresh dirty_fn >/dev/null 2>&1 && cairn def --no-refresh base_fn >/dev/null 2>&1 && cairn def --no-refresh bbase_fn >/dev/null 2>&1 && echo TC006_PASS'`

## TC-007 — Install/uninstall manage both hooks idempotently
- **Story**: US3 · **Traces to**: FR-004
- **Given** a git workspace
- **When** the user installs hooks twice, then uninstalls
- **Then** both the post-commit and post-checkout hooks end up installed once (executable, no duplicated cairn block after the repeat install), and uninstall removes exactly those two, leaving nothing behind
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC007.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && cairn hooks install >/dev/null 2>&1 && C1=$(cksum < .git/hooks/post-commit) && K1=$(cksum < .git/hooks/post-checkout) && cairn hooks install >/dev/null 2>&1 && test -x .git/hooks/post-commit && test -x .git/hooks/post-checkout && [ "$(cksum < .git/hooks/post-commit)" = "$C1" ] && [ "$(cksum < .git/hooks/post-checkout)" = "$K1" ] && cairn hooks uninstall >/dev/null 2>&1 && test ! -e .git/hooks/post-commit && test ! -e .git/hooks/post-checkout && echo TC007_PASS'`

## TC-008 — Refuse-to-clobber: a pre-existing non-cairn hook is never touched (standing guard)
- **Story**: US3 · **Traces to**: FR-004
- **Given** a workspace whose post-commit hook was authored by the user (not cairn-managed)
- **When** the user installs cairn hooks
- **Then** the user's hook survives byte-identical (the installer skips it rather than overwriting) — pinned existing behavior, extended to both hooks, never weakened
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC008.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && printf "#!/bin/sh\necho user_hook\n" > .git/hooks/post-commit && chmod +x .git/hooks/post-commit && cp .git/hooks/post-commit "$T/user_hook.sh" && cairn hooks install >/dev/null 2>&1; cmp -s "$T/user_hook.sh" .git/hooks/post-commit && echo TC008_PASS'`

Standing verify (existing behavior, cited from survey): `uv run --extra test pytest tests/test_install_uninstall_fidelity.py -q -k hook` — probe-verified: 12 passed in 5 s; the `-k hook` selector was probed and is non-empty.

## TC-009 — Marker removal rule: uninstall removes only cairn-managed hooks (standing guard)
- **Story**: US3 · **Traces to**: FR-004
- **Given** a workspace with a user-authored post-checkout hook and cairn hooks installed alongside it
- **When** the user uninstalls cairn hooks
- **Then** the cairn-installed hook is removed and the user's hook survives byte-identical
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC009.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && printf "#!/bin/sh\necho mine\n" > .git/hooks/post-checkout && chmod +x .git/hooks/post-checkout && cp .git/hooks/post-checkout "$T/mine.sh" && cairn hooks install >/dev/null 2>&1 && cairn hooks uninstall >/dev/null 2>&1; cmp -s "$T/mine.sh" .git/hooks/post-checkout && test ! -e .git/hooks/post-commit && echo TC009_PASS'`

Standing verify: `uv run --extra test pytest tests/test_install_uninstall_fidelity.py -q -k hook`

## TC-010 — One-command setup: init with the hooks flag installs both, same rules
- **Story**: US3 · **Traces to**: FR-005, AC1
- **Given** a git workspace with a pre-existing user-authored post-commit hook
- **When** the user runs init with the hooks flag (opt-in, never prompting), twice
- **Then** the cairn post-checkout hook is installed, the user's post-commit hook is still untouched (same refuse-to-clobber rule as the standalone installer), and the repeat run changed nothing further
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC010.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && printf "#!/bin/sh\necho mine\n" > .git/hooks/post-commit && chmod +x .git/hooks/post-commit && cp .git/hooks/post-commit "$T/mine.sh" && cairn init --with-hooks >/dev/null 2>&1 && cairn init --with-hooks >/dev/null 2>&1; test -x .git/hooks/post-checkout && cmp -s "$T/mine.sh" .git/hooks/post-commit && echo TC010_PASS'`

## TC-011 — Opt-in: plain init installs nothing (standing guard)
- **Story**: US3 · **Traces to**: FR-005
- **Given** a git workspace
- **When** the user runs init without the hooks flag
- **Then** no hook is written — hook installation never becomes a default side effect
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC011.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && cairn init >/dev/null 2>&1 && test ! -e .git/hooks/post-commit && test ! -e .git/hooks/post-checkout && echo TC011_PASS'`

## TC-012 — Non-git workspace: hook installation fails cleanly
- **Story**: US3 · **Traces to**: FR-006
- **Given** a directory that is not a git repository
- **When** the user runs the hooks installer
- **Then** the command fails with guidance naming the git-repository requirement, and no hook files or partial artifacts are written anywhere
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC012.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && cairn hooks install >out.txt 2>&1; test $? -ne 0 && grep -qiE "git|repositor" out.txt && test ! -d .git && test ! -e post-commit && test ! -e post-checkout && echo TC012_PASS'`

## TC-013 — Non-git workspace: init with the hooks flag reports guidance, writes no partial hooks
- **Story**: US3 · **Traces to**: FR-006, FR-005
- **Given** a directory that is not a git repository
- **When** the user runs init with the hooks flag
- **Then** the user is told the hooks need a git repository, and no hook artifacts exist. The pinned core is "guidance + nothing half-written"; the store-init side is deliberately not over-pinned
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC013.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && cairn init --with-hooks >out.txt 2>&1; grep -qiE "git|repositor" out.txt && test ! -d .git && test ! -e post-commit && test ! -e post-checkout && echo TC013_PASS'`

## TC-014 — Abuse case: a hostile repository name can never inject shell into a hook
- **Story**: US3 · **Traces to**: NFR-001
- **Given** an attacker creates a git repository whose directory name is a shell payload
- **When** hooks are installed and both installed hook scripts are then executed
- **Then** the payload never executes (the name is rejected up front, or fully neutralized in the rendered scripts); this holds for every hook cairn installs, from any install path
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC014.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && mkdir "x'\''; touch BOMB; '\''x" && cd "x'\''; touch BOMB; '\''x" && git init -q . && cairn hooks install >/dev/null 2>&1; if [ -e .git/hooks/post-commit ]; then sh .git/hooks/post-commit >/dev/null 2>&1; fi; if [ -e .git/hooks/post-checkout ]; then sh .git/hooks/post-checkout >/dev/null 2>&1; fi; test ! -e BOMB && echo TC014_PASS'`

## TC-015 — Foreground overhead: the git operation stays fast and clean while the update runs
- **Story**: US1 · **Traces to**: NFR-003, FR-002, AC2
- **Given** hooks installed in a synced workspace
- **When** a commit fires the hook
- **Then** the commit completes successfully within a normal interactive budget and its output shows no update chatter or tracebacks (the update runs detached; its output is discarded)
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC015.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && git config user.email t@t && git config user.name t && printf "def base_fn():\n    pass\n" > a.py && git add a.py && git commit -qm c0 >/dev/null && cairn init --with-hooks >/dev/null 2>&1 && printf "def more_fn():\n    pass\n" >> a.py && S=$(date +%s) && git commit -aqm c1 >commit_out.txt 2>&1 && E=$(date +%s) && test $((E-S)) -lt 10 && ! grep -qiE "traceback|cairn update" commit_out.txt && echo TC015_PASS'`

A bounded toy repo cannot by itself distinguish a fast synchronous update
from a detached one; the detach property is additionally pinned by TC-016
(a synchronous hook that propagates failure turns TC-016 red). At-scale
standing verify: commit with hooks installed on a real repository and
confirm the commit returns in its usual time.

## TC-016 — Reliability: a failed background update never fails the git operation
- **Story**: US1 · **Traces to**: NFR-004, AC2
- **Given** hooks installed, and the update engine made unable to run (here: invoked from an environment where the cairn command does not exist — the requirement's "missing store"-class failure)
- **When** a commit fires the hook
- **Then** the commit still succeeds (best-effort hook: errors swallowed, exit status unaffected)
- **Pass condition**: `sh -c 'R=$(pwd); T=$(mktemp -d /tmp/fhTC016.XXXXXX); mkdir "$T/shim"; printf "#!/bin/sh\necho \"\$*\" >> \"%s/log.txt\"\nexec uv run --no-sync --project \"%s\" cairn \"\$@\"\n" "$T" "$R" > "$T/shim/cairn"; chmod +x "$T/shim/cairn"; export PATH="$T/shim:$PATH" CAIRN_HOME="$T/home"; cd "$T" && git init -q r && cd r && git config user.email t@t && git config user.name t && printf "def base_fn():\n    pass\n" > a.py && git add a.py && git commit -qm c0 >/dev/null && cairn init --with-hooks >/dev/null 2>&1 && G=$(dirname "$(command -v git)") && printf "def extra_fn():\n    pass\n" >> a.py && PATH="$G" git commit -aqm c2 >/dev/null 2>&1 && echo TC016_PASS'`

## TC-017 — Concurrency guard: background updates stay safe alongside other writers (standing guard)
- **Story**: US1 · **Traces to**: NFR-004
- **Given** the existing concurrency contract (serialized writers, busy-timeout) that hook-fired background updates ride on
- **When** the standing suite runs
- **Then** it passes unchanged — hook-driven background updates must not regress concurrent-write safety
- **Pass condition**: `uv run --extra test pytest tests/ -q -k "incremental and not infra"`

Standing verify cited from survey, probe-verified this session: 89 passed,
1 skipped, in 23 s; the `-k "incremental and not infra"` selector is
non-empty.

## Coverage matrix

<!-- Every FR and applicable NFR appears; check.py fails a requirement with no TC. -->

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-002, TC-003 | auto |
| FR-002      | TC-001, TC-002, TC-004, TC-015 | auto |
| FR-003      | TC-005, TC-006 | auto |
| FR-004      | TC-007, TC-008, TC-009 | auto |
| FR-005      | TC-010, TC-011, TC-013 | auto |
| FR-006      | TC-012, TC-013 | auto |
| NFR-001     | TC-014 | auto |
| NFR-002     | — | — (not applicable per spec: no new data leaves the store) |
| NFR-003     | TC-015 | auto |
| NFR-004     | TC-004, TC-016, TC-017 | auto |
| NFR-005     | — | — (not applicable per spec: hook output discarded by design; existing health surfaces unchanged) |
| NFR-006     | — | — (not applicable per spec: no UI surface touched) |

**Expected current state**: all 17 pass conditions are auto and verified
against the landed feature set by the audit proofs run this session.
