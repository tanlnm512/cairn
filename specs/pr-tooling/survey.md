# Survey: pr-tooling

**Created**: 2026-10-05 | **Baseline**: 0.21.2 @ 0ccce8c
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "No `cairn prs` command exists"
  evidence:   grep -rn "prs\|gh pr" src/cairn --include="*.py"   (no match — verified this session)
  status:     TODO
  verify:     cairn prs --help   (exits non-zero: no such command)
  gap:        whole command; gh subprocess integration is new to src/

item S2: "Diff→symbols machinery already exists as `cairn blast --base` — PR impact is a reuse"
  evidence:   src/cairn/cli/blast.py:18 `@click.option("--base", default=None, help="Compare against this ref's merge base.")`
              src/cairn/graph/blast.py:161 `def _changed_files(conn, workspace: Path, base: str | None) -> tuple[dict, list[dict]]:` :135 `def _resolve_base(repo: Path, base: str) -> str:` :83 `def _parse_diff(text: str) -> list[dict]:` :37 `def _git(repo: Path, args: list[str]) -> str:`
  status:     DONE
  verify:     python3 -m pytest tests/ -q -k blast and not infra
  gap:        PR impact reuses changed-files + symbol resolution + impact_analysis; add PR-number → branch/base resolution on top

item S3: "Impact analysis with depth caps is the blast-radius engine"
  evidence:   src/cairn/graph/traversal.py:157 `def impact_analysis(`
  status:     DONE
  verify:     python3 -m pytest tests/test_traversal_parity.py -q
  gap:        none — blast already drives it; pr-tooling renders its output per PR

item S4: "Community tables are the --conflicts input — provided by the symbol-communities spec, not here"
  evidence:   specs/symbol-communities/spec.md:75 FR-001 (`cairn communities` persists `communities`/`symbol_communities` derived tables)
  status:     TODO
  verify:     sqlite3 "$STORE_DB" "SELECT COUNT(*) FROM communities"   (0 until spec 2 lands)
  gap:        graceful-absent contract per FR-004 (omit section + hint); this spec consumes, never builds

item S5: "Subprocess invocation precedent exists (no gh usage anywhere yet)"
  evidence:   src/cairn/mcp_server/lifecycle.py:116 `r = subprocess.run(` and :131 (launchctl plumbing)
  status:     DONE
  verify:     grep -rn '"gh"' src/cairn --include="*.py"   (no match → new integration)
  gap:        gh JSON calls (pr list/pr status/pr view) with defensive parsing against pinned fixtures

item S6: "CLI wiring is import-side-effect; read-only command convention established"
  evidence:   src/cairn/cli/__init__.py:1-12 one-import-per-module registration; read-only query surfaces (grep cli/grep.py:11 `@main.command(name="grep")`) never open write conns
  status:     DONE
  verify:     cairn --help
  gap:        new cli/prs.py module + one import line

item S7: "Store-age surfacing precedent: status resource reports last_build_age"
  evidence:   src/cairn/mcp_server/_server_core.py:310 `def _health_block(conn)` (degradations, pending_sync, last_build_age) — per the on-demand-paths/http serving surveys
  status:     DONE
  verify:     grep -n "last_build_age\|build_age" src/cairn/mcp_server/_server_core.py | head -3
  gap:        FR-007's store-age line reuses the same build-age source (or files-table mtime) — pin which in tech-spec
```

## Supporting evidence

- gh JSON surfaces this spec consumes (via subprocess): `gh pr list --json number,title,headRefName,author,state,statusCheckRollup,reviewDecision`, `gh pr view <n> --json ...`, `gh pr diff <n>` — shapes pinned as fixtures in tech-spec, parsed defensively (assumption in spec.md; verified available on this machine: `gh --version`).
- PR base for impact: `gh pr view --json baseRefName` → `_resolve_base` reuse; the PR diff itself comes from `gh pr diff`, NOT local git (the local checkout may not have the PR branch).
- Unindexed-file surfacing: `_changed_files` already separates changed entries from graph hits (blast.py:161 tuple return) — FR-003's unindexed listing rides it.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
