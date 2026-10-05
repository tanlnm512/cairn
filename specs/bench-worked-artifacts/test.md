# Test Cases: bench-worked-artifacts

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.
Every auto case anchors itself at the repository root with
`cd "$(git rev-parse --show-toplevel)"` — the project behind the
`uv run --no-sync cairn` launcher (no global install is assumed; the command
a manifest records is the bare `cairn` argv).
"Minimal deterministic run" = `uv run --no-sync cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1` — the cheapest deterministic suite invocation (dep-free hash embeddings, smallest synthetic corpus, fewest repeats; seconds, not minutes).
"Worked sandbox" = a `mktemp -d` directory under the system temp dir standing in for the repo or benchmarks root, so no case writes into the real tree; every case is self-cleaning (removes its sandbox on exit). A sandbox "repo" recreates the canonical benchmarks-root shape there — a `benchmarks/` directory holding `baselines/` and the inventory `README.md` — and launches cairn through `uv run --no-sync --project <repo root>` so the sandbox, not the real tree, is the working repo.
"Bundle" = the three artifacts of FR-001: the raw result JSON, the inputs manifest, and the README companion.
Read-side cases (TC-002) do not mint a bundle; they read the already-committed bundle at `benchmarks/worked/perf-DS-v1`.
These are infrastructure-tier cases: every assertion is structure or determinism only — no wall-clock, throughput, or timing assert appears anywhere in this suite.

## TC-001 — A worked run ships its evidence: result JSON, inputs manifest, companion README
- **Story**: US1 · **Traces to**: FR-001 (AC1)
- **Given** a worked sandbox and a minimal deterministic run
- **When** the user runs the bench with `--worked` pointed at a directory
- **Then** that directory holds the raw result JSON, an inputs manifest, and a README companion — and the companion names every other file present
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && uv run --no-sync cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/bundle" >"$d/out" 2>&1 && test -f "$d/bundle/README.md" && test "$(find "$d/bundle" -type f ! -name "README.md" | wc -l | tr -d " ")" -ge 2 && ok=""; for f in "$d/bundle"/*; do case "${f##*/}" in README.md) ;; *) grep -qF "${f##*/}" "$d/bundle/README.md" || exit 1; python3 -m json.tool "$f" >/dev/null 2>&1 && ok=1 || true ;; esac; done; test -n "$ok"`

## TC-002 — The manifest is self-describing: suite, dataset version, repeats, backend, machine stamp, command line
- **Story**: US1 · **Traces to**: FR-001
- **Given** the committed worked bundle shipped under `benchmarks/worked/perf-DS-v1`
- **When** a reader opens the bundle's inputs manifest
- **Then** it states the suite that ran, the dataset version, the repeat count, the embedding backend, the machine stamp, and the full command line that produced the run
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && test -f benchmarks/worked/perf-DS-v1/manifest.json && test -f benchmarks/worked/perf-DS-v1/perf.json && test -f benchmarks/worked/perf-DS-v1/README.md && grep -q '"suite": "perf"' benchmarks/worked/perf-DS-v1/manifest.json && grep -q '"dataset_version": "DS-v1"' benchmarks/worked/perf-DS-v1/manifest.json && grep -q '"repeats": 3' benchmarks/worked/perf-DS-v1/manifest.json && grep -q '"embed_backend": "hash"' benchmarks/worked/perf-DS-v1/manifest.json && grep -q '"machine_profile"' benchmarks/worked/perf-DS-v1/manifest.json && grep -q -- '"--suite"' benchmarks/worked/perf-DS-v1/manifest.json && grep -q -- '"--worked"' benchmarks/worked/perf-DS-v1/manifest.json && grep -q '^cairn bench' benchmarks/worked/perf-DS-v1/README.md`

## TC-003 — The bundle's raw result is the same payload `--save` writes
- **Story**: US1 · **Traces to**: FR-001 (AC1)
- **Given** two minimal deterministic runs of the same suite and inputs — one saved the baseline way, one bundled the worked way
- **When** the two raw result payloads are compared
- **Then** they carry the same payload structure (same field set, same sections); per-run values may differ only in the advisory timing fields
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && uv run --no-sync cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --save "$d/base.json" >/dev/null 2>&1 && uv run --no-sync cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/bundle" >/dev/null 2>&1 && test -s "$d/base.json" && python3 -c "import json,glob,sys;base=json.load(open(sys.argv[1]));ks=sorted(base);hit=[p for p in glob.glob(sys.argv[2]+'/*.json') if sorted(json.load(open(p)))==ks];sys.exit(0 if hit else 1)" "$d/base.json" "$d/bundle"`

## TC-004 — Pointed at a benchmarks root, each run gets its own suite-datasetversion directory
- **Story**: US1 · **Traces to**: FR-002
- **Given** a worked sandbox repo whose benchmarks root carries the inventory README
- **When** a minimal deterministic run completes with `--worked` pointed at that root
- **Then** a worked area exists under it holding one new directory named for the suite and dataset version (perf-…), and that directory holds the bundle
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && r="$PWD" && mkdir -p "$d/repo/benchmarks/baselines" && printf '# Benchmarks\n\nInventory of committed artifacts.\n' > "$d/repo/benchmarks/README.md" && cd "$d/repo" && uv run --no-sync --project "$r" cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked benchmarks >/dev/null 2>&1 && test -d benchmarks/worked && ls benchmarks/worked | grep -q "^perf-" && test -n "$(find benchmarks/worked -name "README.md")"`

## TC-005 — Pointed at a specific directory, the bundle lands exactly there (created on demand)
- **Story**: US1 · **Traces to**: FR-002
- **Given** a target directory path that does not exist yet
- **When** a minimal deterministic run completes with `--worked` pointed at it
- **Then** the target is created and the bundle sits directly inside it — no extra per-run subdirectory is invented beneath it
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && uv run --no-sync cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/bundle" >/dev/null 2>&1 && test -f "$d/bundle/README.md" && test -z "$(find "$d/bundle" -mindepth 1 -type d)"`

## TC-006 — Re-running the same run overwrites it cleanly — never appends, never leaves debris
- **Story**: US1 · **Traces to**: FR-002
- **Given** a worked sandbox where one minimal deterministic run already wrote its bundle
- **When** the identical run writes to the same target again
- **Then** the set of files in the bundle is unchanged (same names, same count — overwritten, not appended) and no partial/temporary files remain behind
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && uv run --no-sync cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/run" >/dev/null 2>&1 && find "$d/run" -type f | sed "s|$d/run/||" | sort > "$d/l1" && uv run --no-sync cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/run" >/dev/null 2>&1 && find "$d/run" -type f | sed "s|$d/run/||" | sort > "$d/l2" && diff "$d/l1" "$d/l2" && ! find "$d/run" -type f \( -name "*.tmp" -o -name "*.part" -o -name "*.bak" \) | grep -q .`

## TC-007 — Every new artifact gets exactly one inventory row, keyed by its repo-relative path
- **Story**: US2 · **Traces to**: FR-003 (AC1)
- **Given** a benchmarks root whose inventory README exists with no worked rows
- **When** a minimal deterministic run completes pointed at that root
- **Then** the inventory gains one row per new bundle artifact, and each row keys the artifact by its path from the benchmarks root (never a bare filename) with a companion note
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && r="$PWD" && mkdir -p "$d/repo/benchmarks/baselines" && printf '# Benchmarks\n\nInventory of committed artifacts.\n' > "$d/repo/benchmarks/README.md" && cd "$d/repo" && uv run --no-sync --project "$r" cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked benchmarks >/dev/null 2>&1 && test "$(grep -cE "worked/perf-[^/ ]+/[^/ ]+" benchmarks/README.md)" -ge 2`

## TC-008 — Re-runs never duplicate inventory rows
- **Story**: US2 · **Traces to**: FR-003 (AC1)
- **Given** a benchmarks root where a minimal deterministic run already appended its inventory rows
- **When** the identical run writes the same run again into the same root
- **Then** the inventory is unchanged — the same artifacts are not indexed twice
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && r="$PWD" && mkdir -p "$d/repo/benchmarks/baselines" && printf '# Benchmarks\n' > "$d/repo/benchmarks/README.md" && cd "$d/repo" && uv run --no-sync --project "$r" cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked benchmarks >/dev/null 2>&1 && cp benchmarks/README.md "$d/snap" && uv run --no-sync --project "$r" cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked benchmarks >/dev/null 2>&1 && diff "$d/snap" benchmarks/README.md`

## TC-009 — A worked run touches nothing in the inventory except its own appended rows
- **Story**: US2 · **Traces to**: FR-004
- **Given** a benchmarks root holding a pre-existing inventory row for a committed baseline and a separate companion notes document
- **When** a minimal deterministic run completes pointed at that root
- **Then** the inventory changes by additions only — no existing row is edited, reordered, or removed — and the companion notes document is byte-for-byte untouched
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && r="$PWD" && mkdir -p "$d/repo/benchmarks/baselines" && printf '# Benchmarks\n\n| Path | Note |\n|---|---|\n| benchmarks/baselines/DS-v1.1/perf.json | Committed baseline |\n' > "$d/repo/benchmarks/README.md" && printf 'campaign notes\n' > "$d/repo/benchmarks/NOTES.md" && cp "$d/repo/benchmarks/README.md" "$d/before" && cp "$d/repo/benchmarks/NOTES.md" "$d/notes-before" && cd "$d/repo" && uv run --no-sync --project "$r" cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked benchmarks >/dev/null 2>&1 && ! cmp -s "$d/before" benchmarks/README.md && ! diff "$d/before" benchmarks/README.md | grep -q "^<" && cmp -s "$d/notes-before" benchmarks/NOTES.md && grep -q "baselines/DS-v1.1/perf.json" benchmarks/README.md`

## TC-010 — Two worked runs at once into one benchmarks root both succeed and never corrupt the inventory
- **Story**: US2 · **Traces to**: FR-003, FR-002
- **Given** a benchmarks root, and two identical minimal deterministic runs started at the same time pointed at it
- **When** both complete
- **Then** both succeed, and the inventory holds each artifact path at most once — no duplicated or interleaved rows from the concurrent appends
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && r="$PWD" && mkdir -p "$d/repo/benchmarks/baselines" && printf '# Benchmarks\n' > "$d/repo/benchmarks/README.md" && cd "$d/repo" && (uv run --no-sync --project "$r" cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked benchmarks >"$d/a" 2>&1 & p1=$!; uv run --no-sync --project "$r" cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked benchmarks >"$d/b" 2>&1 & p2=$!; wait "$p1" && wait "$p2") && test "$(grep -cE "worked/perf-" benchmarks/README.md)" -le 4 && ! sort benchmarks/README.md | uniq -d | grep -q "worked/"`

## TC-011 — Re-running the manifest's command reproduces the run's deterministic numbers
- **Story**: US1 · **Traces to**: FR-001 (AC2)
- **Given** a completed worked run whose bundle carries the exact command line that produced it, from a run started with a repo-relative worked target in a worked sandbox repo
- **When** that recorded command is taken from the bundle and re-run from the same working directory
- **Then** it completes green, and the operation-count and estimated-token figures in the re-run's raw result equal the original's — only the advisory timing fields may drift
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && r="$PWD" && mkdir -p "$d/repo/benchmarks/baselines" && cd "$d/repo" && uv run --no-sync --project "$r" cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked benchmarks >"$d/r1" 2>&1 && cmd=$(grep -m1 '^cairn bench' benchmarks/worked/perf-DS-v1/README.md) && test -n "$cmd" && cp -r benchmarks/worked "$d/snap" && eval "uv run --no-sync --project \"$r\" $cmd" >"$d/r2" 2>&1 && python3 -c "import sys;exec(\"import json,sys\ndef flat(o,p,out):\n if isinstance(o,dict):\n  for k,v in o.items(): flat(v,p+'.'+k,out)\n elif isinstance(o,list):\n  for i,v in enumerate(o): flat(v,p+'.'+str(i),out)\n else: out.append((p,o))\ndeny=('_ms','ops_per_sec','db_path','db_size','timestamp')\na=[]\nb=[]\nflat(json.load(open(sys.argv[1])),'',a)\nflat(json.load(open(sys.argv[2])),'',b)\na=[(k,v) for k,v in a if not any(t in k for t in deny)]\nb=[(k,v) for k,v in b if not any(t in k for t in deny)]\nsys.exit(0 if a and a==b else 1)\")" "$d/snap/perf-DS-v1/perf.json" benchmarks/worked/perf-DS-v1/perf.json`

## TC-012 — Standing guard: the manifest never carries absolute home paths or secrets
- **Story**: US1 · **Traces to**: NFR-002
- **Given** a hostile environment: the home directory is redirected to a sentinel path and token-carrying variables hold a sentinel secret, then a minimal deterministic run completes
- **When** the whole bundle is searched afterward
- **Then** the sentinel home path, the sentinel secret, and any absolute home-style path appear nowhere in the bundle — every recorded path is repo-relative or variable-templated (standing guard: fails if the manifest ever starts embedding the writer's home directory or environment secrets)
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && mkdir -p "$d/h" && HOME="$d/h" GH_TOKEN=sekrit-sentinel-9 CAIRN_TOKEN=sekrit-sentinel-9 uv run --no-sync cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/bundle" >/dev/null 2>&1 && ! grep -rqF "$d/h" "$d/bundle" && ! grep -rq "sekrit-sentinel-9" "$d/bundle" && ! grep -rqE "/Users/|/home/" "$d/bundle"`

## TC-013 — An unwritable worked target fails cleanly after the run, results still on stdout
- **Story**: US1 · **Traces to**: NFR-004
- **Given** a worked target path that cannot be written (an existing file blocks the directory)
- **When** a minimal deterministic run completes with `--worked` pointed at it
- **Then** the command exits non-zero with a clean failure naming the blocked target — no traceback — and the bench results were still reported on stdout: the run happened first, the bundle failure came after
- **Pass condition**: `cd "$(git rev-parse --show-toplevel)" && d="$(mktemp -d)" && trap 'rm -rf "$d"' EXIT && touch "$d/obstacle" && if uv run --no-sync cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --json --worked "$d/obstacle" >"$d/out" 2>&1; then exit 1; fi && grep -q '"dataset"' "$d/out" && grep -qF "$d/obstacle" "$d/out" && ! grep -qi "traceback" "$d/out"`

## Coverage matrix
<!-- Every FR and applicable NFR appears; `check.py` fails a requirement with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003, TC-011 | auto |
| FR-002      | TC-004, TC-005, TC-006, TC-010 | auto |
| FR-003      | TC-007, TC-008, TC-010 | auto |
| FR-004      | TC-009 | auto |
| NFR-002     | TC-012 | auto (standing guard) |
| NFR-004     | TC-013 | auto |

Not applicable (recorded in spec.md with reasons): NFR-001 (security), NFR-003 (performance), NFR-005 (observability), NFR-006 (accessibility) — no TCs required.

Boundary notes: the empty-input edge (target directory not yet existing) is pinned by TC-005; the concurrent-access edge by TC-010; the hostile-environment edge by TC-012; the unwritable-target edge by TC-013. The huge-corpus edge is deliberately out of this tier — the suite is determinism/structure only, and these cases intentionally use the minimal corpus; at-scale behavior remains the standing bench suite's concern, not this one.

AC trace: US1-AC1 → TC-001, TC-003 · US1-AC2 → TC-011 · US2-AC1 → TC-007, TC-008.
