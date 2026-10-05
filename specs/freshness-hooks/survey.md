# Survey: freshness-hooks

**Created**: 2026-10-04 | **Baseline**: 0.21.2 @ 9cc59b4
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "Post-commit hook template diffs the worktree (HEAD), not the commit range — the blind spot"
  evidence:   src/cairn/hooks/git_hooks.py:33 `cairn update --repo "{repo}" > /dev/null 2>&1 &` (no ref args → update's worktree diff)
              src/cairn/graph/incremental.py:816 "Primary signal: ``git diff --name-only HEAD`` plus untracked source files"
  status:     TODO
  verify:     sed -n '28,36p' src/cairn/hooks/git_hooks.py
  gap:        template must pass a commit-range flag; right after commit the worktree matches HEAD so nothing reindexes

item S2: "No --diff-ref option exists on `cairn update`"
  evidence:   src/cairn/cli/update.py:12 `@click.option("--file", "file_path", ...) ` and :17 `def update(repo, file_path, workspace, db, knowledge):`
  status:     TODO
  verify:     cairn update --help
  gap:        add --diff-ref A..B plumbing into _changed_source_files' git invocation

item S3: "No post-checkout hook anywhere"
  evidence:   src/cairn/hooks/git_hooks.py exports only POST_COMMIT_TEMPLATE (:28), _render_post_commit (:38), install_hooks (:53), uninstall_hooks (:76)
  status:     TODO
  verify:     grep -n "post-checkout\|post_checkout" src/cairn/hooks/git_hooks.py   (no match)
  gap:        new template + install/uninstall coverage; branch vs file checkout decided by $3

item S4: "install/uninstall machinery is idempotent, guarded, and hook-name-agnostic enough to extend"
  evidence:   src/cairn/hooks/git_hooks.py:53 `def install_hooks(repos: List[str], workspace: str) -> List[str]:` (:68 refuses to clobber non-cairn hooks, :71 chmod), :76 `def uninstall_hooks(repos: List[str], workspace: str) -> List[str]:` (removes only when "cairn" in hook text), :16 `def _validate_repo_name(repo: str) -> str:`
  status:     DONE
  verify:     python3 -m pytest tests/test_install_uninstall_fidelity.py -q -k hook
  gap:        callers are post-commit-shaped; generalize per-hook-name

item S5: "`cairn hooks install|uninstall` CLI group exists and is the user surface"
  evidence:   src/cairn/cli/hooks_viz.py:14 `@hooks.command("install")` :16 `def hooks_install(workspace):` :28 `@hooks.command("uninstall")`
  status:     DONE
  verify:     cairn hooks --help
  gap:        surface text says "post-commit hooks" only; extend for post-checkout

item S6: "`cairn init` has no hook flag"
  evidence:   src/cairn/cli/core.py:57 `def init(ws_arg, legacy_dir, no_build, import_docs):`
  status:     TODO
  verify:     cairn init --help
  gap:        add --with-hooks wiring into install_hooks after store creation

item S7: "install-agents --git-hooks rides install_hooks and inherits template changes"
  evidence:   src/cairn/agent_install/__init__.py:443 `if include_git_hooks and target and not dry_run:` :446 `from ..hooks.git_hooks import install_hooks`
  status:     DONE
  verify:     grep -n "include_git_hooks" src/cairn/agent_install/__init__.py
  gap:        none — new templates flow through automatically

item S8: "Update concurrency is hook-safe already (background + WAL + lock)"
  evidence:   src/cairn/graph/incremental.py:414 `conn = get_db(db_path, busy_timeout_ms=20000)` ("waits out lock contention from concurrently-running `cairn serve` processes") :433 `with build_lock(db_path or str(_resolve_store().db)):`
  status:     DONE
  verify:     python3 -m pytest tests/ -q -k "incremental and not infra"
  gap:        none — background hook execution rides this

item S9: "Hook tests pin install/uninstall fidelity; templates themselves lightly pinned"
  evidence:   tests/test_install_uninstall_fidelity.py (install_hooks/git_hooks references), tests/test_graft_parity_repos.py
  status:     DONE
  verify:     python3 -m pytest tests/test_install_uninstall_fidelity.py -q
  gap:        add range-diff + post-checkout cases alongside, never rewrite
```

## Supporting evidence

- Template renderer: src/cairn/hooks/git_hooks.py:38 `def _render_post_commit(repo: str) -> str:` (:44 `POST_COMMIT_TEMPLATE.format(repo=repo)`, :39 "with one quoted" — injection guard is `_validate_repo_name` :16).
- Changed-file detection internals: src/cairn/graph/incremental.py:813 `def _changed_source_files(repo_path: Path, conn=None) -> List[str]:` (:828 "git ran (may still be empty if truly nothing changed). git diff never"); size/mtime fallback follows when git is unavailable.
- Reindex entry: src/cairn/graph/incremental.py:20 `def reindex_paths(`; module docstring :1 "git diff, reindex_paths, and file watcher sync".
- Agent-install dry-run convention: src/cairn/agent_install/__init__.py:388 `include_git_hooks: bool = False` — side effects skipped under dry-run (:443 guard).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
