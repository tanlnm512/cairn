# Pre-Release Checklist

Run through this before tagging a release or merging a non-trivial PR.
Each item maps to a real failure mode hit in this project's history —
don't skip on "I'm sure it's fine."

## Tests
- [ ] `uv run pytest tests/ -q` — full suite (~500 tests). CI runs this too
      (after a `core` smoke pass), but confirm locally before pushing so you
      aren't waiting on a CI round-trip for a local failure. Note the
      pass/skip/fail count; compare to the last green run.
- [ ] No new `xfail` or `skip` slipped in to make a failure "pass."

## Build verification (for changes touching indexing/embedding)
- [ ] `uv run cairn build` succeeds against the real workspace.
- [ ] Spot-check the DB: `sqlite3 <db> "SELECT path FROM files LIMIT 5"` —
      paths are repo-relative (portable), not absolute.
- [ ] If embeddings changed: `uv run cairn embed` runs clean and a
      `semantic_search` returns sane hits.

## "No behavior changed" claims
If a change is supposed to be refactor/doc-only/comments-only:
- [ ] AST-verify: `uv run python -m compileall -q src/` passes, AND run the
      AST comparison helper (see `docs/release-checklist.md` appendix) —
      self-reports of "no code changed" are unreliable; sub-agents and
      late-night edits silently drift into executable code.
- [ ] `git diff` the actual lines — read what changed, don't trust the commit
      message.

## Concurrency / DB locks
- [ ] If schema or write-path changed: start `cairn serve`, then run
      `cairn build` in parallel — must not deadlock or hit "database is locked"
      past the busy_timeout.
- [ ] Read-only MCP daemon (`cairn serve --read-only`) still answers queries
      while a CLI build holds the write lock.

## Portability (the .kg is shared across machines)
- [ ] Stored paths are repo-relative: `SELECT COUNT(*) FROM files WHERE path LIKE '/%'`
      returns 0 after a fresh build.
- [ ] `resolve_file_path` reconstructs an absolute path that exists on disk
      for a sampled symbol.

## Breaking changes
- [ ] Schema migrations are additive (ALTER ADD COLUMN), idempotent, and run
      on a pre-existing DB without error.
- [ ] Public CLI flags / MCP tool signatures haven't changed shape, or the
      change is documented in CHANGELOG.md.
- [ ] `[Unreleased]` in CHANGELOG.md reflects what shipped.

## Agent / bulk-edit safety
If changes were made by sub-agents or bulk find-replace:
- [ ] `make verify-no-code-change` passed (or `make verify-no-code-change REF=HEAD~1`
      for a just-made commit) — comments-only changes verified programmatically
      via AST comparison (`scripts/verify_no_code_change.py`), not by reading diffs.
- [ ] `ruff check` clean.
- [ ] No string literals (help text, print output, error messages) were
      silently rewritten — these are executable, not comments.
