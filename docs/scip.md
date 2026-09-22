# SCIP indexing: compiler-grade edges as an opt-in overlay

← [Docs index](README.md)

Read this when you want exact call/reference edges in languages where
tree-sitter resolution goes `ambiguous`, when configuring the `scip` key in
`cairn.json`, or when diagnosing why a build fell back to tree-sitter.

SCIP (Sourcegraph Code Intelligence Protocol) indexes are produced by
compiler-grade indexers: every reference occurrence carries the definition it
binds to. Cairn is a **consumer** of these indexes, never a producer — indexes
are generated out-of-band (by you or CI) and pointed at via config. During
`cairn build`, each configured index is imported as an **edges-only overlay**
on top of the unchanged tree-sitter graph: symbols and structure always come
from tree-sitter; the index contributes exact `calls`/`references` edges only.
Precise queries (`get_callers`, `impact_analysis`, taint paths) gain exact
edges exactly where name-based resolution abstains.

For the build pipeline this stage slots into, see
[indexing.md](indexing.md); for `cairn.json` parsing rules, see
[configuration.md](configuration.md).

## The overlay model

- **Edges only.** The importer never writes symbols. `imports`, `contains`,
  and `decorates` edges stay tree-sitter-only; only `calls`/`references` are
  affected.
- **Per-file authority.** A file covered by an index has its tree-sitter
  `calls`/`references` edges replaced by the index's edges (`source='scip'`).
  Files outside every index keep their tree-sitter edges unchanged.
- **Position join, both ends.** Each occurrence's line range joins to the
  innermost tree-sitter symbol whose range contains it, in the same file.
  Edges never attach by symbol-name or USR string equality — opaque USRs
  (Swift) and version-unstable symbol strings (scip-python) are precisely what
  a name join cannot survive.
- **Resolution vocabulary unchanged.** A target defined in the workspace is
  `exact`; stdlib/external targets are `unresolved` (same labels as
  tree-sitter resolution).
- **Edge kinds.** An edge whose target symbol is a function or method is
  `calls`; every other edge is `references`.
- **The index wins disagreements.** Where the index's binding and the
  resolver's would disagree, the edge follows the index (compiler-grade) and
  the disagreement is counted — surfaced in the build summary, never silently
  resolved either way.
- **Multi-repo workspaces.** Every document in an index is attributed through
  its repo-relative path, so one workspace-level index may cover many repos.
- **Validate-then-write.** An index is fully parsed and joined before any edge
  is written; a corrupt index aborts cleanly rather than half-importing.

## Install (cairn side)

The protobuf runtime ships as an extra:

```bash
pip install "cairn-intel[scip]"
```

The extra floor-pins `protobuf` against the vendored SCIP bindings stub
(`src/cairn/parsers/_scip_pb2.py`), so installing the extra is the whole
setup. Regenerating the stub (`scripts/regen_scip_pb2.sh`) is a dev-only step
and never needed at install time. Without the extra — or with a protobuf
runtime older than the stub requires — the build succeeds tree-sitter-only
and records the fallback (see below).

## Configuration

In `cairn.json` (workspace root), the `scip` key maps each language to its
index file:

```json
{
  "scip": {
    "indexes": {
      "python": "index.scip",
      "typescript": "build/ts.scip"
    }
  }
}
```

- Keys are language names (`python`, `typescript`, `java`, …). Any language
  can point at an existing index; automatic generation (below) exists only
  for the seven registry languages.
- One index per language per workspace is the supported shape. Per-repo
  indexes in a multi-repo workspace go through `cairn import-scip`.
- `cairn config` echoes the resolved `scip` indexes.
- A malformed `scip` key warns and is ignored — a bad config never breaks the
  build.

**Manual import.** Against an already-built DB, `cairn import-scip <file>`
runs the same overlay rules as the build path — useful for CI-generated
indexes and per-repo indexes. The DB must already be built.

## Per-indexer install

All seven registry languages, with the invocation the registry drives (also
what to run manually in CI):

| Language | Binary | Install / invocation | Notes |
|---|---|---|---|
| python | `scip-python` | `npm install -g @sourcegraph/scip-python` — an **npm** package, not pip (a Node program). Run: `scip-python index . --output index.scip` | releases ship via npm; no pip package exists |
| typescript | `scip-typescript` | `npm install -g @sourcegraph/scip-typescript`. Run from project root: `scip-typescript index --output index.scip` | covers TypeScript and JavaScript |
| java | `scip-java` | See [scip-java](https://github.com/sourcegraph/scip-java). Run from project root: `scip-java index --output index.scip` | requires a Gradle or Maven build |
| kotlin | `scip-java` | Same binary and command as java | the dedicated `scip-kotlin` compiler plugin is **archived** — Kotlin coverage rides scip-java's JVM indexing and is degraded relative to Java |
| go | `scip-go` | `go install github.com/scip-code/scip-go/cmd/scip-go@latest`. Run from the dir holding `go.mod`: `scip-go --output index.scip` | |
| rust | `rust-analyzer` | `RA_SCIPOUT=index.scip rust-analyzer scip .` — output path comes from the `RA_SCIPOUT` env var | upstream describes its SCIP support as limited; crate dependencies may need a `cargo vendor` workaround |
| swift | `scip-swift` | Build from source on a Mac — **macOS/Xcode only**. Run: `scip-swift index . --output index.scip` | no canonical upstream repo exists today; only third-party homebrew taps (e.g. `phuongddx/homebrew-scip-swift`, `jarvis-intelligence/scip-swift`) — verify provenance before installing third-party binaries |

Each entry degrades independently: one language's missing or broken indexer
never affects another's.

## Automatic generation

When a language is configured in `scip.indexes` but the index file is absent
and the indexer binary is on `PATH`, `cairn build` generates it:

- **Exactly one attempt per build** — a failed attempt is not retried until
  the next build.
- **An existing index is never rebuilt.** You (or CI) own the regeneration
  cadence; cairn will not re-run a compiler on every build.
- **Bounded**: each indexer run is capped at 30 minutes.
- Everything about generation is best-effort: any failure falls back to
  tree-sitter for that language with an observable record, and the tool's
  install hint prints under `cairn build -v`.

## Fallback behavior

SCIP can never break a build. Every failure mode degrades to tree-sitter with
an observable record on the skip surface (`skipped_files`, aggregated by
reason in `cairn stats`; generation/runtime failures record the configured
index path as the row's path — a red flag by design, since it is not a source
file):

| Condition | Record (`skipped_files` reason) | Result |
|---|---|---|
| `[scip]` extra absent, or protobuf runtime older than the stub | `scip_runtime_missing` | build proceeds tree-sitter-only, with the `[scip]` install hint |
| Indexer binary not on `PATH` | `scip_gen_missing_binary` | tree-sitter edges for that language |
| Indexer exits nonzero or produces no file | `scip_gen_nonzero_exit` | same |
| Indexer exceeds the 30-minute cap | `scip_gen_timeout` | same |
| OS error launching the indexer | `scip_gen_os_error` | same |
| Index file corrupt / unparseable | `scip_parse_error` | no import (nothing partial was written) |
| A document's occurrences mostly fail the position join (rate < 0.5) | `scip_join_anomaly` | that file keeps its tree-sitter edges — a low join rate means a stale index or wrong file mapping, not noise |
| Document path matches no indexed file | drop counters in the import record | document skipped |

## Incremental updates

`cairn update` never invokes indexers. Changed files in covered languages are
re-parsed via tree-sitter, so their edges' provenance flips to `tree_sitter` —
observable in the stats `edge_sources` counts — and the next full
`cairn build` restores `scip` edges for them. Updates stay fast and never
stall on a compiler; provenance always reflects reality.

## Measuring the uplift

- `cairn stats` reports edge provenance (`edge_sources`: `tree_sitter` /
  `scip` counts) and `exact_share_by_language` inside its resolution-share
  surface — build the same tree with the index on and off to measure
  per-language exact-share uplift.
- The `cairn build` summary adds a `scip: N edges (D disagreements)` line
  when scip edges exist.
- Exactness is not traded for correctness: where the index and the resolver
  disagree, the edge follows the index and the disagreement is counted, so
  the uplift introduces no false-exact edges.
