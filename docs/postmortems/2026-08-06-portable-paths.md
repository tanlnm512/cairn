# Postmortem: portable-path storage — the half-finished design

> Registry entry: `docs/BUGS.md#2026-08-06/portable-path-stale-comments`
> Date: 2026-08-06 · Commit: `29c6e62`

## TL;DR

The codebase was *designed* to store file paths repo-relative so the `.kg`
database would be portable across machines. Comments in `watcher.py` and
`incremental.py` already described this behavior, and fallback logic existed to
handle the relative form. But the builder — the one place that actually writes
paths — stored absolute paths. The design was half-implemented: the read side
anticipated relative paths, the write side never conformed, and the comments
became lies that made the inconsistency invisible.

## The discovery

A requirement came in: *after embedding, share the `.kg` file; on another
machine, queries should resolve paths relative to that machine, not the
origin.* Empirical inspection of a real DB showed:

```
files.path: /Users/tan.le/Projects/cairn/src/cairn/paths.py   (absolute)
repos.path: /Users/tan.le/Projects/cairn                        (absolute)
```

Yet `graph/watcher.py:83` already read:

> *"The path in the DB may be repo-relative (build stores relative paths);
> resolve against the repo root before stat-ing."*

And `graph/incremental.py:72`:

> *"Path normalization: build stores RELATIVE paths (e.g. 'service.py'
> relative to the repo root), while reindex_paths receives ABSOLUTE paths..."*

Both comments were aspirational — written for the intended design, describing
a state that the builder never produced. A second stale belief compounded it:
both files claimed `repo_id=''` for single-repo workspaces, when the builder
actually stored the directory basename (`"cairn"`). The comments had drifted
from reality undetected because **no test asserted the actual stored values**.

## Why it happened

A design decision was committed to comments before the implementation caught
up. Three independent facts, all wrong, reinforced each other's invisibility:

1. **The builder discarded `rel_path`.** `scanner.FileInfo` already computed
   `rel_path` (relative to repo root) — the data was *available* at the exact
   point it was needed. But `insert_parsed_file` stored the absolute `path`
   instead. The right data was one field away; it just wasn't used.
2. **Read-side code coped with the mismatch.** `watcher.py` had an
   `if not p.is_absolute(): p = repo_path / row["path"]` fallback. Because that
   branch never fired (paths were always absolute), the inconsistency produced
   no runtime symptom — the code "worked."
3. **Comments described intent, not reality.** Someone reading the comments
   would believe relative storage was already implemented. The lie was
   authoritative.

The general failure mode: **a design decision documented before it's enforced
becomes a lie the moment reality diverges, and nothing keeps it honest.**

## The fix

Complete the design. Storage and resolution are now split by responsibility:

- **Write side** (`builder.py`): store repo-relative paths unconditionally.
  `files.path`, `repos.path`, `parse_errors.file_path`, `skipped_files.path`,
  `pending_sync.path` all store relative forms. `FileInfo.rel_path` — the data
  that was always there — is now what gets stored.
- **Read side** (`scanner.resolve_file_path`): one chokepoint that resolves a
  stored relative path to absolute for disk I/O, via
  `resolve_repo_path(workspace, repo_id) / stored_path`. Every disk-touching
  consumer (`explore.py`, `embeddings.py`, `watcher.py`, `cli/system.py`,
  `incremental.py`) reads through it.
- **Backward compat**: `resolve_file_path` passes legacy absolute paths through
  unchanged, so an un-rebuilt DB keeps working until the next `cairn build`
  converts its rows.

The stale comments were corrected or removed. The `SELECT ALL` fallback in
`watcher.py` (originally papering over the phantom `repo_id=''`) was
re-commented accurately.

## The lesson

Two principles, both recurring:

1. **Don't document the future as the present.** If a design isn't implemented,
   say so in the comment (`# TODO: store relative paths`) — or don't write the
   comment. A comment describing the intended state reads as authoritative fact
   and will mislead every reader (human or agent) until reality catches up.
2. **A comment asserting a runtime value is a debt — pay it with a test.**
   "build stores repo_id=''" should have been backed by
   `assert row["repo_id"] == ""`. Comments about runtime behavior rot silently;
   tests fail loudly. The invariant tests added here
   (`tests/test_invariants.py`) exist specifically to make this rot visible.

## What to ask yourself

When you see a comment like *"X stores Y"* or *"Z returns W"*:

- Is there a test asserting Y/W? If not, the comment is unverified.
- Does the code actually do what the comment says? Read the write site, not
  just the read site.

If the answer to either is "no," you've found the same failure mode — a design
half-finished, documented as done.
