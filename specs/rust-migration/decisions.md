# Decisions: rust-migration dependency batch (Constitution C-03)

C-03 (specs/CONSTITUTION.md:14): no new runtime dependency without a recorded
`D-###` decision stating why and what it costs. This file records the P0-4
first batch (plan.md §4, §10) and the provisional later-batch crates.

All versions below were queried live from crates.io / GitHub on **2026-10-18**
(method: `curl https://crates.io/api/v1/crates/<name>`, sparse-index and
`raw.githubusercontent.com` manifest fetches, GitHub API repo lookups). Pins
are exact (`=x.y.z` in Cargo.toml). Any bump or feature addition is a new or
amended D-###, and grammar bumps additionally pass the 2a node-type
revalidation gate (plan.md §12, "Grammar-crate drift").

Parity principle for pins: the Python implementation is the behavioral
spec until the flip, so each grammar pin targets the **same upstream parser.c
release** the pinned Python package ships (pyproject.toml:29-43), not the
newest crates.io release. Newer releases exist for several grammars; they are
adopted only through 2a revalidation.

---

## D-001: rmcp =3.5.1 — MCP SDK for the Rust daemon

- **What**: `rmcp` pinned `=3.5.1`, features `default` (= `base64`, `macros`,
  `server`) + `transport-streamable-http-server`. The `auth` feature stays
  **off**: `auth = ["dep:async-trait", "dep:oauth2", "__reqwest", "dep:url"]`
  (rmcp v3.5.1 Cargo.toml:198) pulls the OAuth2 stack cairn does not use —
  bearer-key auth remains cairn-owned middleware, matching the Python-side
  ruling (specs/archive/2026-10-07-http-mcp-serving/tech-spec.md D-002) and
  plan.md §5 1c (key resolution order is contract C-5).
- **Why**: the streamable-HTTP server transport for Phase 1's daemon (plan.md
  §5 1c). `transport-streamable-http-server` composes
  `transport-streamable-http-server-session` + `server-side-http` +
  `transport-worker` (Cargo.toml:186-190). Official modelcontextprotocol SDK
  (modelcontextprotocol/rust-sdk) — the plan's hedge against SDK churn
  (plan.md §12 "rmcp major-version churn").
- **Churn flag**: rmcp moved 1.x → 3.x in ~2 years (3.5.1 published
  2026-10-05). Goldens (C-2 transport fixtures) are the guard; the pin is
  exact so upgrades are deliberate.
- **Cost**: license Apache-2.0. Pure Rust (tokio/tower/http-family +
  schemars/serde). Binary-size delta not measurable yet (no Rust build
  exists) — record it at P0-5 when the spike builds. Platform coverage:
  unrestricted (pure Rust; fits the P0-1 matrix incl. musl).
- **P0-5 amendment — spike direct deps**: the daemon crate declares the
  rmcp dependency stack directly to build the P0-5 spike, at the versions
  rmcp 3.5.1 itself resolves (Cargo.lock): `axum 0.8`
  (default-features off, `http1`+`tokio` — rmcp's own manifest pins), `tokio 1`
  (`rt-multi-thread`/`net`/`macros`/`signal`), `tokio-util 0.7` (type of
  `StreamableHttpServerConfig::cancellation_token`), `serde_json 1`
  (rmcp model types). Test-only: `regex 1` (fixture normalization port).
  No new pins — all resolve inside the D-001 stack already in Cargo.lock;
  cargo-deny advisories/licenses gate them from P0-1. Measured delta: the
  P0-5 example binary linking the full stack (rmcp + axum/hyper + bundled
  SQLite + sqlite-vec, strip=true, aarch64-macOS release) is **6,115,808 B
  (~5.8 MB)** (`cargo build --release -p cairn-daemon --example serve_search`).

## D-002: rusqlite =0.40.2, feature `bundled` — the `.kg` store

- **What**: `rusqlite` pinned `=0.40.2` with exactly `features = ["bundled"]`
  (which implies `modern_sqlite`; pulls `libsqlite3-sys 0.38.2`).
- **Why**: every store/query path reads and writes the `.kg` SQLite file
  (contract C-1). `bundled` compiles one SQLite build into the binary, giving
  a single SQLite version across all platforms — deterministic behavior for
  goldens and canonical dumps instead of the per-OS system-library matrix.
  FTS5 (required by `symbols_fts`, C-1) is enabled by the bundled build:
  libsqlite3-sys 0.38.2 `build.rs:157-163` passes `-DSQLITE_ENABLE_FTS5`
  (plus FTS3/JSON1/RTREE) — verified at rusqlite tag v0.40.2. No extra
  feature needed.
- **Features deliberately not enabled**: `load_extension` (vec0 is linked
  statically, D-003), `functions`/`trace`/`vtab`/`hooks` — add only with a
  new D-### if the port needs them.
- **Cost**: license MIT. C compile via `cc` on every target (C toolchain
  required; musl-static friendly, matching the P0-1 Linux leg). Binary size:
  the bundled SQLite amalgamation lands in the binary — measure the real
  delta in the P0-1 release dry-run build (no honest proxy exists on this
  machine: macOS system libsqlite3 lives in the dyld shared cache).

## D-003: sqlite-vec =0.1.9 — vec0 vector indexes, statically linked

- **What**: `sqlite-vec` pinned `=0.1.9` (the newest stable; 0.1.10-alpha.4
  exists and is not taken — plan.md §12 "sqlite-vec is pre-v1: pin").
- **Why**: C-1's per-model `vec0` tables must read what Python wrote. The
  Python side is locked at sqlite-vec **0.1.9** (uv.lock), so both
  implementations run the identical upstream extension version — same vec0
  on-disk format and SQL surface by construction. The crate compiles
  `sqlite-vec.c` with `SQLITE_CORE` (bindings/rust/build.rs at v0.1.9) and
  exposes `sqlite3_vec_init`; registration is one
  `sqlite3_auto_extension(Some(sqlite3_vec_init))` call before connections
  open (the crate's own `test_rusqlite_auto_extension` demonstrates it), so
  rusqlite needs no `load_extension` feature and there is no dlopen of a
  wheel-provided dylib (Python's path: src/cairn/graph/ann_index.py:111-115).
- **Cost**: license MIT/Apache-2.0. C compile via `cc` (same toolchain
  requirement as D-002). Binary size: 158 KB measured (`vec0.dylib` shipped
  in the Python wheel, aarch64-macOS, same version — static link lands in the
  same order). Platform coverage: anywhere `cc` runs, including musl.

## D-004: tree-sitter =0.26.13 — parser runtime (core)

- **What**: `tree-sitter` pinned `=0.26.13`, default features. Transitive
  normal deps: `tree-sitter-language ^0.1` (the grammar-ABI bridge all
  grammar crates use), `regex`, `streaming-iterator`; `bindgen`/`wasmtime`
  stay optional-off (crates.io dependency list for 0.26.13).
- **Why**: the Python runtime is pinned `tree-sitter==0.26.0`
  (pyproject.toml:29, uv.lock) and every recorded golden was produced under
  the 0.26 line — staying on 0.26 keeps the Rust runtime in the same
  family. crates.io has no 0.26.0 (earliest in-line 0.26.2); 0.26.13
  (2026-08-23) is the newest 0.26.x patch. Moving to 0.27.x (newest 0.27.1)
  is a deliberate later D-### gated on 2a revalidation, not a default.
- **Cost**: license MIT. Pure Rust → unrestricted platform coverage. Binary
  size: 332 KB measured for the compiled PyPI runtime binding
  (`tree_sitter/_binding.cpython-312-darwin.so`, aarch64) — order-of-magnitude
  proxy; the Rust static figure gets recorded at P0-1.

## D-005: tree-sitter grammar crates — 15 languages, pinned to the Python-side parser.c releases

Pins target the releases in pyproject.toml:30-43 (+ the vendored Kotlin),
because those parser.c builds are what the goldens and `.kg` content encode
(plan.md §12). Sources: crates.io version lists (per crate), PyPI JSON API
(upstream repo attribution), GitHub API (repo activity/commits).

| # | Language | Python pin (pyproject.toml) | crates.io state (2026-10-18) | Rust pin | Flag |
|---|----------|------------------------------|------------------------------|----------|------|
| 1 | python | `tree-sitter-python==0.23.6` :31 | newest 0.25.0; 0.23.6 available | `=0.23.6` | parity pin; newer exists, adopt via 2a only |
| 2 | java | `tree-sitter-java==0.23.5` :30 | newest 0.23.5 (2024-12-21) | `=0.23.5` | exact match |
| 3 | typescript | `tree-sitter-typescript==0.23.2` :33 | newest 0.23.2 (2024-11-11) | `=0.23.2` | exact match |
| 4 | javascript | `tree-sitter-javascript==0.23.0` :34 | newest 0.25.0; 0.23.0 available | `=0.23.0` | parity pin |
| 5 | swift | `tree-sitter-swift==0.7.3` :32 | newest 0.7.4 (2026-10-04); 0.7.3 available; same upstream (alex-pinkus) both sides | `=0.7.3` | parity pin; upstream active |
| 6 | go | `tree-sitter-go==0.25.0` :37 | newest 0.25.0 (2025-08-29) | `=0.25.0` | exact match |
| 7 | php | `tree-sitter-php==0.24.1` :38 | **0.24.1 does not exist on crates.io** (0.23.11 → 0.24.2 → 0.25.1) | `=0.24.2` | **GAP**: nearest same-minor release; 2a revalidation mandatory |
| 8 | ruby | `tree-sitter-ruby==0.23.1` :39 | newest 0.23.1 (2024-11-11) | `=0.23.1` | exact match |
| 9 | rust | `tree-sitter-rust==0.24.2` :40 | newest 0.24.2 (2026-03-27) | `=0.24.2` | exact match |
| 10 | c-sharp | `tree-sitter-c-sharp==0.23.1` :41 | newest 0.23.5; 0.23.1 available | `=0.23.1` | parity pin |
| 11 | c | `tree-sitter-c==0.23.4` :42 | newest 0.24.2; 0.23.4 available | `=0.23.4` | parity pin |
| 12 | cpp | `tree-sitter-cpp==0.23.4` :43 | newest 0.23.4 (2024-11-11) | `=0.23.4` | exact match |
| 13 | objc | `tree-sitter-objc==3.0.2` :36 | newest 3.0.2 (2024-12-16), same upstream (tree-sitter-grammars) both sides | `=3.0.2` | exact match, **no drift** — the crates.io publish is older but is the identical release Python pins |
| 14 | dart | `tree-sitter-dart==0.1.0` :35 — PyPI package from **efrenbl/tree-sitter-dart** (PyPI project URLs; repo tag v0.1.0; active, pushed 2026-10-16) | crates.io `tree-sitter-dart` 0.1.0/0.2.0 is **nielsenko/tree-sitter-dart — a different grammar** (independent repo, not a fork) | **vendored generated sources @ efrenbl v0.1.0** behind a thin workspace crate exposing `tree_sitter_language::LanguageFn` | **DIVERGENCE**: see below |
| 15 | kotlin | vendored **fwcd/tree-sitter-kotlin** @ `1852ea17b7f60fb3f9d84e0b1555d56b46b39fb1` (NOTICE:174 — upstream main HEAD 2026-08-02, vendored 2026-08-25; built via setup.py:1-15; no PyPI pin) | crates.io newest 0.3.8 (2024-08-03) — ~2 years behind upstream (repo active, pushed 2026-10-16; the pinned commit's manifest says version 0.4.0, unpublished; 0.3.8 requires tree-sitter `>=0.21,<0.23`) | **cargo git dependency pinned to commit `1852ea17b7f60fb3f9d84e0b1555d56b46b39fb1`** | **STALE**: crates.io crate unusable for parity |

- **Dart ruling**: the crates.io crate is a different grammar than the Python
  side parses with — pinning it would silently swap implementations against
  every dart golden. The upstream repo ships only an old-style Rust binding
  (its Cargo.toml at v0.1.0 depends on `tree-sitter = "0.22.6"` as a normal
  dep and `lib.rs` returns `tree_sitter::Language` — type-incompatible with
  the 0.26 core), so a git dependency cannot be used as-is. Vendor the
  generated parser at tag v0.1.0 (the tag matching the PyPI 0.1.0 package)
  and wrap it in a workspace crate — the established kotlin precedent.
- **Kotlin ruling**: the fwcd grammar at the pinned commit is already the
  modern style (`tree-sitter-language = "0.1"`, per its Cargo.toml at
  `1852ea17…`), so a commit-pinned git dependency is exact-parity with the
  vendored sources Python builds today, with no wrapper work.
- **Grammar-crate `tree-sitter` deps are dev-only** (verified for
  tree-sitter-python 0.23.6, tree-sitter-java 0.23.5, tree-sitter-php 0.24.2,
  tree-sitter-c 0.23.4 via the crates.io dependencies API), so they do not
  constrain the D-004 core pin.
- **Cost**: all 15 grammars are MIT. Generated C compiled by `cc` per target
  (same toolchain requirement as D-002/D-003). Measured compiled-parser sizes
  (aarch64-macOS wheels of the exact pinned releases, from
  `.venv/lib/python3.12/site-packages`): c 649 KB, c-sharp 5,294 KB, cpp
  3,410 KB, dart 1,231 KB, go 261 KB, java 439 KB, javascript 424 KB,
  objc 5,262 KB, php 2,074 KB, python 488 KB, ruby 2,119 KB, rust 1,151 KB,
  swift 3,736 KB, typescript 2,862 KB (Σ ≈ 28.7 MB) + compiled kotlin
  extension 5,730 KB (`src/cairn/_tree_sitter_kotlin.abi3.so`) → **~34 MB of
  parser tables** that will be statically linked into the single binary.
  Exact Rust-linked delta (LTO, dead-strip) gets recorded at P0-1; these are
  wheel-side proxies, deliberately not ported as binary numbers.

## D-006: clap =4.6.7 — CLI framework (Phase 3a)

- **What**: `clap` pinned `=4.6.7`, features `default` (= `std`, `color`,
  `help`, `usage`, `error-context`, `suggestions`) + `derive`
  (`derive = ["dep:clap_derive"]`, clap v4.6.7 Cargo.toml:190).
- **Why**: the Phase 3a command-tree port replaces click
  (pyproject `click>=8.0`); derive keeps commands declarative. The C-4
  machine contracts (stdout-pure-JSON modes, exit codes, `--json/--db`
  flags) are framework-independent and gated by CLI goldens, not by clap.
- **Cost**: license MIT OR Apache-2.0. Pure Rust → unrestricted platforms.
  Binary size: small single-digit-MB order; record the measured delta at 3a.

## Provisional later-batch entries

Pins recorded now per plan §10; re-pin at the phase's entry and convert to
firm D-### when the crate actually joins the build and cargo-deny starts
gating it.

## D-007 (provisional): notify =8.2.0 — file watching (Phase 2d)

- **What**: `notify` pinned `=8.2.0` (newest stable; a 9.0.0-rc.6 line exists
  and is not taken). Default features (per-platform backends incl.
  `macos_fsevent`/`macos_kqueue`).
- **Why**: 2d incremental + watcher (plan.md §6), replacing the watchdog
  extra (pyproject `watch = ["watchdog>=3.0"]`).
- **Cost**: license **CC0-1.0** — flag: the P0-1 cargo-deny license
  allowlist must include CC0-1.0 explicitly. Pure Rust → unrestricted
  platforms. Graft semantics parity is the 2d gate, not the crate choice.

## D-008 (provisional): prost =0.14.4 — protobuf runtime for SCIP import (Phase 2g)

- **What**: `prost` pinned `=0.14.4`.
- **Why**: 2g SCIP import (plan.md §6). Replaces the Python arrangement of
  the checked-in `_scip_pb2.py` stub + `protobuf` runtime (pyproject.toml
  scip extra, :128-139); Rust side generates types at dev time via
  prost-build (dev-only) and ships only the `prost` runtime, preserving the
  run-once auto-gen registry behavior.
- **Cost**: license Apache-2.0. Pure Rust → unrestricted platforms.

## D-009 (provisional): minijinja =3.0.1 — dashboard templates (Phase 3b)

- **What**: `minijinja` pinned `=3.0.1`, default features.
- **Why**: 3b dashboard serving (plan.md §6), replacing `jinja2>=3.0`
  (pyproject.toml:47); required for embedding templates in the single binary.
- **Cost**: license Apache-2.0. Pure Rust → unrestricted platforms.
  Compatibility flag: minijinja is Jinja2-compatible, not byte-identical —
  3b gates on reviewed human-readable output diffs (plan.md §7 3b), and any
  template needing a minijinja-incompatible construct is a spec amendment,
  not a template rewrite.

## D-010 (provisional): yaml-rust2 =0.13.0 — `.knowledge` bundle frontmatter (1b-km)

- **What**: `yaml-rust2` pinned `=0.13.0`, `default-features = false` (drops
  the `encoding` feature / `encoding_rs`; concepts are UTF-8 markdown).
- **Why**: the `.knowledge` bundle (contract C-3) stores every concept as
  markdown with YAML frontmatter written by Python's `yaml.safe_dump`
  (nested signal dicts, promotion-history lists, workflow steps). The 1b-km
  tool port reads and writes those files, so a real YAML 1.2 parser is
  required; hand-rolling one is not credible against PyYAML emitter output
  (flow folding at width 80, quoting of timestamp/number-like scalars).
  Emission stays cairn-owned: a conservative block-style writer that
  single-quotes anything ambiguous, so PyYAML re-parses Rust-written
  frontmatter to identical values.
- **Cost**: license MIT OR Apache-2.0 (inside the P0-1 allowlist). Pure Rust →
  unrestricted platforms. New crate in the lockfile (~3 crates with
  `saphyr`/`reserved-hashmap` internals) — cargo-deny gates it once wired.
- **Companion pin**: `regex = "1"` promoted from transitive (D-004
  tree-sitter dep, already in Cargo.lock at 1.13.1) to a direct cairn-query
  dependency — the ported privacy redaction, session-bookkeeping, and
  router intent patterns are regex-defined in the Python source. No new
  lockfile entry. `serde_json = "1"` same story (D-001 amendment stack).

## D-011: sha2 =0.10.9 — hash-embedding backend (1b-search)

- **What**: RustCrypto `sha2` pinned `=0.10.9`, default features, for the
  SHA-256 token hashing inside the deterministic `hash-256-v1` embedder
  (`_hash_vec` in src/cairn/graph/embeddings.py). The 0.11 line exists
  (0.11.1) and is not taken; 0.10.9 is the newest 0.10.x patch.
- **Why**: semantic_search's dense leg must embed queries identically to the
  Python `hash` backend or every hash-pinned golden diverges. SHA-256 is the
  one primitive the hash backend needs; the resolved tree has no SHA-256
  (Cargo.lock checked) and hand-rolling one is not credible.
- **Cost**: license MIT OR Apache-2.0 (inside the P0-1 allowlist). Pure Rust →
  unrestricted platforms incl. musl. Transitive: `digest`, `block-buffer`,
  `crypto-common`, `generic-array`, `typenum`, `cpufeatures` — all
  MIT OR Apache-2.0; binary delta single-digit KB.


---

## D-012: rayon =1.12.0 — parse pool for the build path (2c)

- **What**: `rayon` pinned `=1.12.0` (newest stable; 1.10.0/1.11.0 exist and
  are not taken), default features. Direct transitive additions to the
  lockfile: `rayon-core 1.13.0`, `crossbeam-deque 0.8.8`,
  `crossbeam-epoch 0.9.21`, `crossbeam-utils 0.8.23`.
- **Why**: plan.md 2c mandates the rayon pool as the ProcessPoolExecutor
  replacement ("a rayon thread pool over shared memory removes that overhead
  class", spec.md). The `cairn-build` builder parses files on a
  `ThreadPoolBuilder` pool sized by the `build_graph` `workers` argument
  (None = CAIRN_WORKERS, then cpu count, clamped 1..=256, mirroring the
  Python `_parse_all` cap), and restores scan order with a stable sort before
  any insert so output is independent of parse completion order.
- **Cost**: license MIT OR Apache-2.0 (inside the P0-1 allowlist; the
  crossbeam stack is MIT OR Apache-2.0). Pure Rust → unrestricted platforms
  incl. musl. No unsafe introduced by cairn crates.

---

## Roll-up

- **License allowlist for cargo-deny (P0-1)**: `Apache-2.0`, `MIT`,
  `MIT OR Apache-2.0`, `MIT/Apache-2.0`, `CC0-1.0` (notify only).
- **Toolchain classes**: C-compiling crates (rusqlite bundled, sqlite-vec,
  15 grammars) need `cc` on every release target; pure-Rust crates (rmcp,
  tree-sitter core, clap, notify, prost, minijinja) have no platform
  constraints. Both classes fit the P0-1 matrix (aarch64/x86_64 ×
  macOS/Linux, musl static for Linux); Windows is out of matrix per the
  0.14.0 platform drops (C-03 rationale).
- **Pin upgrade rule**: every version bump or feature flip is a new/amended
  D-###; grammar bumps additionally pass the 2a node-type revalidation gate
  before landing.
- **Open measurements** (record at P0-1/P0-5): bundled-SQLite binary delta,
  rmcp dependency-stack delta, exact Rust-linked grammar-table sizes.
