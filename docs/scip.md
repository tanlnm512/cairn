# Hybrid SCIP / Tree-sitter Indexing

cairn can consume pre-built **SCIP** (Sourcegraph Code Intelligence Protocol)
indexes for languages where compiler-grade symbol bindings beat tree-sitter's
heuristic resolver — Kotlin, Java, Swift, and TypeScript in particular. Cairn
stays a **consumer** of SCIP indexes; it never generates them (with one bounded
exception for a missing index — see [Automatic generation](#automatic-generation-opt-in)
below).

When an index is configured **and present**, `cairn build` skips tree-sitter
parsing for that language and imports the SCIP data instead (exact resolution —
every reference names the definition it points to). When the index is absent
or undeclared, cairn falls back to tree-sitter for that language. Both can
coexist in the same workspace.

## 1. Generate an index (out-of-band)

Install the relevant compiler-backed indexer and point it at your repo:

```bash
# Kotlin (scip-java — the canonical Java+Kotlin indexer; scip-kotlin is merged in)
scip-java index --output build/scip/kotlin.scip

# TypeScript (scip-typescript)
scip-typescript index --output build/scip/typescript.scip

# Swift (scip-swift)
scip-swift index /path/to/repo --output build/scip/swift.scip

# Python (scip-python — npm package, not pip)
scip-python index . --output=build/scip/python.scip

# Go (scip-go)
scip-go --output=build/scip/go.scip

# Rust (rust-analyzer scip subcommand)
rust-analyzer scip . --output build/scip/rust.scip
```

See the [SCIP indexer list](https://github.com/sourcegraph/scip#indexers) for
Scala (via scip-java), and others. The output is a protobuf `.scip` file.

> Commit the `.scip` file (or produce it in CI) — cairn reads it at build time
> and never triggers regeneration.

### Swift / scip-swift notes

`scip-swift` builds the target repo with indexing enabled
(`swift build --enable-index-store` or
`xcodebuild ... COMPILER_INDEX_STORE_ENABLE=YES`), then reads the resulting
IndexStore. Two consequences worth knowing:

- **macOS only.** Indexing anything that imports Apple-only frameworks
  (`UIKit`, `WatchKit`, `WidgetKit`) requires Xcode + the iOS/watchOS SDK, which
  Apple ships only on macOS. Generate the index on a Mac; cairn can consume the
  `.scip` file on any platform.
- **Opaque symbol identity.** scip-swift embeds the compiler's raw USR (e.g.
  `_$s5Hello7GreeterC7sayHelloyySSF`) as an opaque, escaped descriptor rather
  than a demangled `Hello.Greeter.sayHello()` chain. Cross-references still
  resolve exactly (USR is compiler-guaranteed unique), but the symbol strings
  are not human-readable the way `scip-kotlin`'s are.
- **No call-specific role.** SCIP's `SymbolRole` has no call bit, so Swift call
  sites are marked `ReadAccess` like any other reference. cairn therefore tags
  Swift call edges as `kind='reference'`, not `kind='call'` — call-graph queries
  (`get_callers`, `impact_analysis` keyed on `call` edges) will under-report for
  Swift. This is inherent to the SCIP spec, not a bug.

Build/install it from source on a Mac: <https://github.com/phuongddx/scip-swift>

## Automatic generation (opt-in)

Normally you generate the index out-of-band (CI, a make target) and commit it.
As a convenience, if `cairn.json` declares a SCIP index for a language but the
**file is missing** and a known indexer binary is on `PATH`, `cairn build` will
run the indexer once to produce it before importing. This is the one bounded
exception to "cairn never generates indexes".

- **Bounded.** An existing index is never rebuilt — the user (or CI) owns the
  regeneration cadence. Generation triggers only when the index is configured
  *and absent*.
- **Silent fallback.** If the indexer is missing, fails, times out, or exits
  nonzero, cairn logs it (visible under `cairn build -v`) and falls back to
  tree-sitter for that language. A generation failure never breaks the build.

Known indexers cairn can drive:

| Language     | Tool              | Install / source                                            |
|--------------|-------------------|-------------------------------------------------------------|
| `swift`      | `scip-swift`      | <https://github.com/phuongddx/scip-swift> (macOS/Xcode)     |
| `java`       | `scip-java`       | <https://github.com/sourcegraph/scip-java> (Gradle/Maven)   |
| `kotlin`     | `scip-java`       | same as Java — scip-kotlin is merged into scip-java         |
| `typescript` | `scip-typescript` | <https://github.com/sourcegraph/scip-typescript>            |
| `python`     | `scip-python`     | npm `@sourcegraph/scip-python`; <https://github.com/sourcegraph/scip-python> |
| `go`         | `scip-go`         | `go install ...@latest`; <https://github.com/scip-code/scip-go> |
| `rust`       | `rust-analyzer`   | `rust-analyzer scip` subcommand; <https://github.com/rust-lang/rust-analyzer> |

> **Mixed Java + Kotlin projects.** `scip-java` is the canonical indexer for
> *both* Java and Kotlin — one `scip-java index` run indexes mixed `.java` +
> `.kt` sources into a single `.scip`, with each `Document.language` tagged per
> source file. Declare both keys pointing at the same file:
> `{"scip": {"java": "build/scip/jvm.scip", "kotlin": "build/scip/jvm.scip"}}`.
> The orchestrator's idempotency check ensures the shared file is generated
> once (whichever key runs first creates it; the second sees it exists and
> skips).

Languages without a usable single-binary SCIP indexer (Ruby, C/C++,
Objective-C, C#, Haskell, OCaml) are not auto-generated; a committed index for
them is still consumed unchanged. Dart and PHP indexers exist but lack an
`--output` flag (they write `index.scip` in the CWD), so they aren't in the
auto-generation registry — generate those out-of-band and commit the result.

A committed or CI-produced index always wins — generation only fills gaps.

## 2. Declare it in `cairn.json`

Map each language to its index file, relative to the workspace root:

```json
{
  "scip": {
    "kotlin": "build/scip/kotlin.scip",
    "typescript": "build/scip/ts.scip"
  }
}
```

At build time, cairn resolves each path and keeps only languages whose file
**actually exists** → that set drives the tree-sitter skip. A missing file is
the fallback (tree-sitter for that language), not an error.

## 3. Build

```bash
cairn build
```

No new flags — discovery is config-driven. The summary panel reports which
languages used SCIP and how many symbols came from each, or omits the line
entirely when no SCIP data was imported:

```
╭─ Built graph in 12.3s ───────────────────────────────────────────────────────╮
│ ...                                                                          │
│ edges resolved: 48,210 exact (91%)  ·  2,104 ambiguous  ·  2,593 unresolved │
│ SCIP: kotlin (8,402 sym), typescript (3,117 sym)                             │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Verifying the wiring

`cairn config` echoes the resolved SCIP config and whether each index file
exists, so you can tell at a glance which languages will use SCIP vs
tree-sitter:

```
scip (/path/to/cairn.json):
  kotlin: build/scip/kotlin.scip  (exists)
  typescript: build/scip/missing.scip  (MISSING (falls back to tree-sitter))
```

## The standalone escape hatch

`cairn import-scip <file>` imports a single index into an already-built DB
without running a full build. Useful for iterating on an indexer's output:

```bash
cairn import-scip build/scip/kotlin.scip --repo myrepo --format proto
cairn import-scip legacy-index.json --format json   # legacy JSON shape
```

## What about edits? (incremental updates)

SCIP indexes are generated out-of-band, so when you edit a SCIP-covered file,
`cairn update` / the file watcher / MCP catch-up re-parse that **single file**
with tree-sitter (tagged `source='tree_sitter'`) — the same as the "missing
index" fallback. The file's symbols/edges lose exact resolution until the next
full `cairn build` re-imports the regenerated SCIP index for that language.

This is a bounded, self-healing staleness window — not a silent permanent
downgrade. The alternative (leave stale SCIP rows untouched) would mean edited
files silently stop reflecting their current content, which is worse for
correctness than a temporary precision dip.

## Dependency model

No PyPI package ships Sourcegraph's SCIP protobuf bindings — the `scip` name
on PyPI is an unrelated bioimaging library
([sourcegraph/scip#259](https://github.com/sourcegraph/scip/issues/259), open).
cairn vendors a checked-in generated stub (`src/cairn/parsers/_scip_pb2.py`)
and depends on the real `protobuf` runtime via the optional `[scip]` extra:

```bash
uv tool install 'cairn-intel[scip]' --force
# or
pip install cairn-intel[scip]
```

**Version coupling.** The vendored stub embeds a
`ValidateProtobufRuntimeVersion(...)` check pinned to the `protobuf` version it
was generated against (see the header comment in `_scip_pb2.py`). The `[scip]`
extra's `protobuf` floor is kept in sync with that check — if you regenerate
the stub, re-pin the floor in `pyproject.toml` together (see below). A missing
runtime, or one too old for the stub, degrades to "SCIP extra not installed"
with the install hint rather than crashing the build — tree-sitter-only builds
never import the stub.

## How paths are resolved (multi-repo)

SCIP indexers are typically invoked from inside a repo and emit
`Document.relative_path` relative to their own `Metadata.project_root` (the
repo dir), not the workspace root. The importer reads
`index.metadata.project_root` and resolves each document's path through the
scanner so SCIP rows land under the correct `(repo_id, repo-relative path)` —
the same file identity the scanner and incremental path use. Indexes that omit
`Metadata.project_root` fall back to treating paths as workspace-relative,
which is correct only when paths happen to be workspace-relative.

The `project_root` value is normalized before use, so different indexers'
conventions all resolve correctly: plain absolute paths (scip-kotlin,
scip-typescript), workspace-relative paths, and `file://`-prefixed URLs
(scip-swift, which writes `URL(fileURLWithPath:).absoluteString`).

## Regenerating the vendored stub

Regeneration is a dev-only step. The stub carries a header pinning the upstream
`scip.proto` commit and generator versions; re-pin together whenever
`grpcio-tools` is upgraded.

```bash
scripts/regen_scip_pb2.sh              # upstream HEAD
scripts/regen_scip_pb2.sh <commit-sha> # pin a specific commit
```

After regenerating, update the `[scip]` extra's `protobuf` floor in
`pyproject.toml` to match the `ValidateProtobufRuntimeVersion()` check at the
top of the generated file, and commit both together.
