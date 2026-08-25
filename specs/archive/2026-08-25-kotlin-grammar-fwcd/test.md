# Test Cases: kotlin-grammar-fwcd

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-25
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Baseline evidence cited verbatim from [survey.md](survey.md):
- `uv run --extra dev --extra test pytest -m core -q` -> 26 passed, 2105 deselected, 1 warning in 33.74s (item 7)
- `uv run --extra dev --extra test pytest tests -q -k kotlin` -> 13 passed, 2118 deselected in 4.45s (items 2, 7)
- `uv run --extra dev --extra test pytest tests/test_build_scip_hybrid.py tests/test_golden_parsers.py tests/test_kotlin_operator_invoke.py tests/test_self_demo.py -q` -> 30 passed in 23.08s (item 6)
- `uv run python -c "import tree_sitter; print(tree_sitter.__version__)"` -> 0.26.0 (item 1)

The observable surface is the `cairn` CLI, the installed package, and pytest.
Cases marked **regression guard** pass at baseline and must keep passing after
the grammar swap. Cases marked **standing** are permanent tripwires that fail
if a removed promise creeps back. The one-time A/B parity procedure (old
grammar vs new grammar on the same corpus) is executed at migration time only;
afterwards the golden snapshot is its automated stand-in.

## US1 — Modern Kotlin indexes correctly

### TC-001 — Kotlin loads and parses through cairn's own vendored grammar
- **Story**: US1 · **Traces to**: FR-001
- **Given** the cairn package is installed from the repository, with Kotlin
  support carried inside cairn itself rather than by the old grammar package
- **When** cairn parses any ordinary Kotlin file (the existing Kotlin test
  selection runs)
- **Then** the Kotlin language loads from cairn's own tree and parsing
  succeeds, on the same tree-sitter runtime version cairn pins today
- **Pass condition** (auto, regression guard):
  `uv run --extra dev --extra test pytest tests -q -k kotlin` reports 13 passed at baseline, zero failures after the swap;
  and `uv run python -c "import tree_sitter; print(tree_sitter.__version__)"` prints 0.26.0.

### TC-002 — Modern-syntax fixture set parses free of ERROR nodes
- **Story**: US1 · **Traces to**: FR-004, US1-AC1
- **Given** a set of `.kt` fixtures, each using syntax newly supported by the
  grammar: KEEP-0438 destructuring, `when` guard conditions, multi-dollar
  string interpolation, and trailing commas — one fixture per construct and
  one combining all four in a single file
- **When** cairn parses each fixture
- **Then** every parse tree contains no ERROR nodes
- **Pass condition** (auto): the fixture set is part of the Kotlin test
  selection and asserts zero ERROR nodes per fixture;
  `uv run --extra dev --extra test pytest tests -q -k kotlin` passes (count grows from the 13 baseline).

### TC-003 — Boundary: each modern construct alone in a minimal file
- **Story**: US1 · **Traces to**: FR-004, US1-AC1
- **Given** four minimal `.kt` files, each containing exactly one modern
  construct and nothing else (a lone destructuring declaration; a single
  `when` with one guard condition; one string with multi-dollar
  interpolation; one declaration whose only modern element is a trailing
  comma)
- **When** cairn parses each file
- **Then** each parse tree is free of ERROR nodes — the construct carries the
  file on its own, with no surrounding code to mask a mis-parse
- **Pass condition** (auto): same Kotlin test selection with the four
  minimal fixtures included and asserting zero ERROR nodes.

### TC-004 — Boundary: empty and comment-only Kotlin files
- **Story**: US1 · **Traces to**: FR-004
- **Given** an empty `.kt` file and a `.kt` file containing only a package
  declaration and comments
- **When** cairn indexes a workspace containing both files
- **Then** indexing completes without parse degradation and attributes no
  invented symbols to either file
- **Pass condition** (auto): covered by fixtures in the Kotlin test
  selection; a symbol lookup over the built index returns nothing for the
  two files.

### TC-005 — Boundary: invalid Kotlin text degrades gracefully
- **Story**: US1 · **Traces to**: FR-004
- **Given** a `.kt` file containing text that is not valid Kotlin at all
- **When** cairn builds the index for the workspace
- **Then** the build completes without crashing, the bad file is accounted
  for as a parse problem rather than silently dropped, and the remaining
  files index normally
- **Pass condition** (manual): the index build command exits without an
  error traceback, and the system status view counts the parse problem for
  that file.

### TC-006 — Boundary: a large modern-syntax corpus parses clean end to end
- **Story**: US1 · **Traces to**: FR-004, US1-AC1
- **Given** a large Kotlin codebase (a real Android/KMP-style repository or
  an aggregated fixture corpus) using modern syntax broadly
- **When** cairn builds the full index over it
- **Then** the build completes and the corpus-wide ERROR-node scan reports
  zero parse-tree degradation
- **Pass condition** (auto): the corpus ERROR-node scan (part of the test
  suite) reports zero; the fast smoke subset stays green:
  `uv run --extra dev --extra test pytest -m core -q` (26 passed at baseline).

### TC-007 — Interface and enum keep their classification
- **Story**: US1 · **Traces to**: FR-002, US1-AC2
- **Given** a `.kt` file declaring an interface and an enum class — the
  shape the existing golden corpus sample already exercises
- **When** cairn extracts symbols with the new grammar
- **Then** the interface is recorded with interface kind and the enum with
  enum kind, identical to what the current grammar produces
- **Pass condition** (auto, regression guard): the golden corpus test passes:
  `uv run --extra dev --extra test pytest tests/test_golden_parsers.py -q`
  (within the 30-passed baseline command).
  One-time A/B check at migration (manual): extraction output for the corpus
  under old and new grammar is diffed and matches.

### TC-008 — Boundary: interface/enum variants with bodies and constructors
- **Story**: US1 · **Traces to**: FR-002, US1-AC2
- **Given** interfaces and enums with non-trivial bodies: an interface with
  default method implementations, an enum with constructor parameters and
  methods, and classes implementing an interface plus an enum
- **When** cairn extracts symbols
- **Then** kind classification stays interface/enum, and members and
  implementations are captured exactly as with the current grammar
- **Pass condition** (auto): these variants join the golden corpus fixtures;
  the golden test selection passes with the expanded expected output.

### TC-009 — Calls, properties, constructor fields, inheritance unchanged
- **Story**: US1 · **Traces to**: FR-003, US1-AC2
- **Given** the existing Kotlin corpus exercising the Android UseCase invoke
  idiom (bare and explicit calls), class-body properties, primary-constructor
  val fields, and inheritance with constructor arguments
- **When** cairn parses and extracts edges with the new grammar
- **Then** call edges, property declarations, primary-constructor fields,
  and inheritance edges are identical to the current grammar's output
- **Pass condition** (auto, regression guard):
  `uv run --extra dev --extra test pytest tests/test_build_scip_hybrid.py tests/test_golden_parsers.py tests/test_kotlin_operator_invoke.py tests/test_self_demo.py -q`
  (30 passed at baseline) and
  `uv run --extra dev --extra test pytest tests -q -k kotlin` (13 passed at
  baseline) both stay green.
  One-time A/B edge-count diff at migration (manual): symbol and edge lists
  under old and new grammar match.

### TC-010 — Boundary: demanding inheritance and property shapes
- **Story**: US1 · **Traces to**: FR-003, US1-AC2
- **Given** Kotlin declarations with the trickiest shapes the corpus covers:
  a class inheriting a generic base class passing constructor arguments while
  also implementing an interface, and properties whose types are qualified
  and nullable
- **When** cairn extracts inheritance edges and property declarations
- **Then** every supertype (generic base and interface) yields an
  inheritance edge and every property is recorded — nothing silently drops
- **Pass condition** (auto): edge-count assertions over these fixtures are
  part of the Kotlin test selection and pass.

## US2 — Grammar updates ship through the existing release flow

### TC-011 — Release wheels carry the compiled Kotlin extension per platform
- **Story**: US2 · **Traces to**: FR-005, US2-AC1
- **Given** the tag-triggered release build has run
- **When** a user on any supported platform (macOS arm64/x64, manylinux
  x86_64/aarch64, musllinux, Windows) downloads cairn's own wheel
- **Then** each wheel is platform-specific and contains the compiled Kotlin
  language extension inside cairn's own package
- **Pass condition** (manual, release-time): for each supported platform,
  the wheel artifact from the release build is platform-tagged (not a
  universal pure-python wheel), installs, and parses a Kotlin file.

### TC-012 — Install succeeds with no C toolchain
- **Story**: US2 · **Traces to**: FR-005, US2-AC2
- **Given** a clean machine on a supported platform with Python but no C
  compiler or build toolchain
- **When** the user runs `pip install cairn-intel`
- **Then** the install succeeds entirely from wheels and cairn works
- **Pass condition** (manual): on the toolchain-free machine,
  `pip install cairn-intel` completes from wheels with no build step, then
  `python -c "import cairn; print('ok')"` prints ok, and indexing a `.kt`
  file produces symbols.

### TC-013 — Standing: the toolchain-free install promise never regresses
- **Story**: US2 · **Traces to**: FR-005, US2-AC2
- **Given** any future released version of cairn
- **When** it is installed with binary-only resolution on each supported
  platform
- **Then** the install still never requires a toolchain — any change that
  reintroduces a source-build requirement for stock installs fails here
- **Pass condition** (manual, standing, per release): on a toolchain-free
  machine per platform, `pip install cairn-intel --only-binary :all:`
  succeeds and `python -c "import cairn; print('ok')"` prints ok.

### TC-014 — Boundary: source build still possible where a toolchain exists
- **Story**: US2 · **Traces to**: FR-005
- **Given** an environment that does have a C toolchain and the source
  distribution
- **When** cairn is built and installed from source
- **Then** the source path still produces a working install (the compiled
  extension builds in-tree)
- **Pass condition** (manual): `pip install cairn-intel --no-binary cairn-intel`
  on the toolchain machine succeeds and Kotlin parsing works afterwards.

### TC-015 — Old grammar dependency removed outright
- **Story**: US1, US2 · **Traces to**: FR-006
- **Given** the updated package installed in the development environment
- **When** the environment and the declared dependencies are inspected
- **Then** the old PyPI Kotlin grammar package is gone — not installed, not
  declared — and Kotlin indexing still works, proving there is exactly one
  grammar path
- **Pass condition** (auto, regression guard):
  `uv pip show tree-sitter-kotlin` reports the package as not found
  (baseline: "Name: tree-sitter-kotlin / Version: 1.1.0");
  `uv run python -c "from tree_sitter_kotlin import language; print(language())"`
  fails with a module-not-found error (baseline: prints a language capsule);
  and the Kotlin test selection still passes.

### TC-016 — Standing: no fallback loader ever returns
- **Story**: US1, US2 · **Traces to**: FR-006
- **Given** any future state of the codebase
- **When** the source tree and dependency manifests are swept for the old
  grammar's module name as a loadable path
- **Then** no runtime loader path or dependency declaration references it —
  a fallback to the old grammar must be impossible, not merely unused
- **Pass condition** (auto, standing):
  `grep -rn "tree_sitter_kotlin" src/ pyproject.toml uv.lock` returns no
  output (baseline: the loader mapping and the pinned dependency are present
  there); historical mentions in license notices or test docstrings are
  inert and out of scope.

## Hybrid SCIP path (US1 substrate)

### TC-017 — scip-java hybrid merge still works on the new substrate
- **Story**: US1 · **Traces to**: FR-007
- **Given** a Kotlin project whose configuration declares a pre-built
  scip-java index
- **When** the hybrid build runs
- **Then** tree-sitter still parses the Kotlin files and the compiler-grade
  SCIP edges merge onto those rows exactly as before
- **Pass condition** (auto, regression guard):
  `uv run --extra dev --extra test pytest tests/test_build_scip_hybrid.py -q`
  passes (within the 30-passed baseline command).

### TC-018 — Boundary: declared SCIP index file absent
- **Story**: US1 · **Traces to**: FR-007
- **Given** a workspace whose configuration declares a pre-built Kotlin SCIP
  index but the index file is missing on disk
- **When** the build runs
- **Then** the build does not crash and Kotlin symbols still come from the
  tree-sitter parse alone
- **Pass condition** (auto): the hybrid test selection stays green with this
  scenario covered; human observation that the build completes and Kotlin
  rows exist without the merge.

## Release hygiene

### TC-019 — Changelog entries land under [Unreleased]
- **Story**: US2 · **Traces to**: FR-008
- **Given** the grammar swap is complete on the feature branch
- **When** a maintainer reads the changelog
- **Then** entries describing the grammar change appear under the
  [Unreleased] section in its existing Keep-a-Changelog subsections, and no
  version number is bumped ahead of tag time
- **Pass condition** (manual): `sed -n '1,45p' CHANGELOG.md` shows the
  kotlin-grammar entries under `## [Unreleased]` (baseline: no
  kotlin-grammar entries exist there yet).

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001     | auto |
| FR-002      | TC-007, TC-008 | auto + one-time manual A/B |
| FR-003      | TC-009, TC-010 | auto + one-time manual A/B |
| FR-004      | TC-002, TC-003, TC-004, TC-005, TC-006 | auto + manual (TC-005) |
| FR-005      | TC-011, TC-012, TC-013, TC-014 | manual (release-time) |
| FR-006      | TC-015, TC-016 | auto (standing) |
| FR-007      | TC-017, TC-018 | auto |
| FR-008      | TC-019     | manual |

No FR is untestable: all eight have at least one observable case. FR-005's
cases are release-time observations because the artifact under test is the
published wheel, not the working tree.
