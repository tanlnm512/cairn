# Known Issues

← [Docs index](README.md)

> Open defects found in the SCIP overlay/import path, not yet fixed. Each
> entry names the symbol, the defect, the evidence, and the failure scenario
> it produces. Use `cairn def <symbol>` or `explore` to jump to current
> source — no line numbers here, they rot.

## `src/cairn/parsers/scip_importer.py`

### `import_scip_file` — partial-transaction data loss on mid-loop failure

`_DELETE_FILE_CALLREF_EDGES` runs immediately per document inside the
planning loop, but the compensating `_INSERT_EDGE` rows for every document
are deferred to a single `executemany()` after the loop finishes.

**Evidence:** the DELETE and the batched INSERT are separated by the rest of
the loop body (further document processing, `_count_disagreements`), so
nothing ties one document's delete to its own insert transactionally.

**Failure scenario:** if `_count_disagreements` (or any other per-document
step) raises partway through the loop, the caller (`_apply_scip_overlay`)
catches the exception and commits the connection to record the skip. That
commit durably persists the DELETEs already executed for earlier documents,
while their batched INSERT never ran — those files permanently lose their
tree-sitter `calls`/`references` edges, violating the overlay's own
"degrades to tree-sitter, never loses edges" contract.

### `_resolve_target` — local-symbol resolution has no per-document scoping

`def_sites` keys definition occurrences by raw SCIP symbol string across the
whole index. `_resolve_target` iterates every `def_sites` entry for a symbol
without checking which document the occurrence itself belongs to.

**Evidence:** SCIP `local` symbols (e.g. `local 0`) are only unique
*within* a document — the spec guarantees they are "never referenced
outside their Document." `def_sites` and `_resolve_target` carry no
per-document restriction for this case.

**Failure scenario:** two files each define `local 0`. An occurrence of
`local 0` in file A walks `def_sites["local 0"]` in insertion order; if
file B's definition entry appears first and joins successfully against
file B's own spans, `_resolve_target` returns that target — a bogus
cross-file edge for what should be a same-file local-variable reference.
Systemic, since locals recur in every function.

## `src/cairn/graph/dataflow.py`

### `_closure_rows` / `maintain_transitive_closure` — full-table scan regresses incremental maintenance

`maintain_transitive_closure` delegates to `_closure_rows`, which
unconditionally reads every row of `symbols` and `edges` and builds full
`adjacency`/`per_kind`/`case2` dicts before `restrict_sources` is applied.

**Evidence:** the prior implementation scoped every query to the affected
ids via temp-table joins; `_closure_rows` applies `restrict_sources` only
when seeding the initial level, never to the reads themselves.

**Failure scenario:** on a large repo, a single-file incremental edit still
materializes the entire graph into Python dicts before narrowing to the
handful of affected ids — reintroducing the O(total graph size) per-edit
cost this function's own docstring says the design exists to avoid. The
closure-gate benchmark only exercises the full-build path, so this
regression on the incremental path ships unmeasured.

## `src/cairn/graph/builder.py`

### `_apply_scip_overlay` — not wired into the incremental build path

`_apply_scip_overlay` is only called from the full build path
(`_build_graph_impl`); the incremental/watch path never references it.

**Evidence:** no reference to `_apply_scip_overlay` or `import_scip_file`
exists outside `_build_graph_impl`.

**Failure scenario:** after a full build imports a SCIP index and replaces a
covered file's `calls`/`references` edges with exact SCIP edges, editing
that file and running the incremental updater silently reverts it to
lower-precision tree-sitter edges — no warning, no `skipped_files` record —
until the next full rebuild re-applies the overlay.

### `_apply_scip_overlay`'s config load — silently swallows failures

The `load_config(workspace)` call is wrapped in `except Exception: return
None`, suppressing any failure with no logging at all.

**Evidence:** the function's own docstring states every degradation prints
via `_log` regardless of `verbose`; this catch path is the one exception.

**Failure scenario:** a transient I/O error re-reading `cairn.json`, or a
future change that makes a malformed `scip` section raise instead of
degrade, causes the overlay to silently no-op — the build summary omits the
`scip` key entirely, indistinguishable from a workspace with no SCIP config
configured, even with `--verbose`.

## `scripts/regen_scip_pb2.sh`

### Header-length guard doesn't match the splice offset

The guard accepts a detected header length of 6, 7, or 8 lines, but the
splice that follows always starts at a literal line 7.

**Evidence:** the guard and the splice use independent constants — the
guard's accepted set doesn't feed the splice's start offset.

**Failure scenario:** a future protoc/grpcio-tools header of exactly 6 or 8
lines passes the guard but still gets spliced at the wrong offset —
dropping a real body line (6-line case) or leaving a stray header line
(8-line case) — silently corrupting the vendored `_scip_pb2.py` despite the
check reporting success.

## `src/cairn/cli/core.py`

### `import_scip` — silent no-op on workspace/db mismatch

`--workspace` defaults independently of `--db`. If `--db`/`CAIRN_DB` points
at a graph built from a different workspace than the cwd, every document
fails `_match_document_path` and is counted as skipped.

**Evidence:** the command has no check comparing `matched_documents` against
`documents` before printing a normal-looking result line.

**Failure scenario:** the CLI still prints a normal "0 edges (0
disagreements)" line with exit code 0, giving no signal that a
workspace/db mismatch — not the index content — caused the no-op; a user
can conclude the index itself is empty or bad.
