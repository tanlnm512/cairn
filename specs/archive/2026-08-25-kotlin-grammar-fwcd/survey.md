# Survey: kotlin-grammar-fwcd

**Created**: 2026-08-25 | **Baseline**: 0.13.0 @ 48d65cb0359419d9b343503fe2531dd6a6d04509 (main)
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item 1: "Kotlin language loading today (_registry.py, tree_sitter_kotlin refs)"
  evidence:   src/cairn/parsers/_registry.py:121  "kotlin": "tree_sitter_kotlin",
              (inside _load_language_module's mapping, lines 119-138)
              src/cairn/parsers/_registry.py:66    return lang_mod.language()
              (default loader path — kotlin is NOT in _SPECIAL_LOADERS, lines 43-52)
              src/cairn/parsers/_registry.py:38    return Parser(Language(capsule))
              src/cairn/parsers/_registry.py:28    from tree_sitter import Language, Parser
              src/cairn/parsers/_registry.py:4-5   Uses tree-sitter 0.26 + per-language wheels (tree-sitter-kotlin,
              pyproject.toml:29                    "tree-sitter==0.26.0",
              pyproject.toml:30                    "tree-sitter-kotlin==1.1.0",
              uv.lock (package block):             name = "tree-sitter-kotlin" / version = "1.1.0" /
                                                    wheels: cp39-abi3 macosx_10_9_x86_64,
                                                    macosx_11_0_arm64, manylinux_2_17_aarch64 (list
                                                    continues past excerpt)
              uv.lock:298                          { name = "tree-sitter-kotlin", specifier = "==1.1.0" },
              Import sites: `import tree_sitter_kotlin` appears NOWHERE in src/ or
              tests/ — the only runtime reference is the mapping string at
              _registry.py:121 (importlib.import_module consumes it).
  status:     DONE
  verify:     uv run python -c "from tree_sitter_kotlin import language; print(language())"
                -> <capsule object "tree_sitter.Language" at 0x109e3ca20>   PASS
              uv run python -c "import tree_sitter; print(tree_sitter.__version__)"
                -> 0.26.0   PASS
              uv pip show tree-sitter-kotlin
                -> Name: tree-sitter-kotlin / Version: 1.1.0   PASS
  gap:        none
```

```
item 2: "KotlinParser node-type walk surface (src/cairn/parsers/kotlin.py)"
  evidence:   Type-decl sets:
              kotlin.py:25-30  TYPE_DECL_NODES = {
                  :26 "class_declaration", :27 "object_declaration",
                  :28 "interface_declaration", :29 "enum_declaration" }
              kotlin.py:33     FUNC_DECL_NODES = {"function_declaration"}
              Interface/enum kind assignment (_classify_type_decl, kotlin.py:192-215):
                  :198  if node.type == "class_declaration":
                  :202-203  txt == "interface" -> return "interface"
                  :204-205  txt == "enum"      -> return "enum"
                  :206-207  txt == "class"     -> return "class"
                  :209-210  object_declaration -> "class"
                  :211-212  interface_declaration -> "interface"
                  :213-214  enum_declaration -> "enum"
              Primary-constructor fields (_prescan_field_types, kotlin.py:86-111):
                  :94  cc.type == "primary_constructor"
                  :96  pc.type == "class_parameters"
                  :98  cp.type == "class_parameter"
                  :102 cc.type == "class_body"
                  :104 member.type == "property_declaration"
              class_parameter walk (visit): kotlin.py:172  if t == "class_parameter"
              Inheritance edges (_parse_inheritance / _collect_inheritance_targets):
                  :381 child.type in ("delegation_specifiers", "inheritance_specifier")
                  :410 node.type == "constructor_invocation"
                  :412/:418 child.type == "user_type" / node.type == "user_type"
              Qualified identifiers (_parse_import):
                  :361  if child.type == "qualified_identifier":
                  :124  if t in ("import", "import_header"):
              type_reference / nullable_type reads (3 sites, always as a triple
              with user_type):
                  :485  elif pc.type in ("user_type", "type_reference", "nullable_type"):   (_param_types)
                  :502  elif vc.type in ("user_type", "type_reference", "nullable_type"):   (_var_name_and_type)
                  :525  elif child.type in ("user_type", "type_reference", "nullable_type"): (_class_param_name_and_type)
              Identifier spellings — every name lookup accepts BOTH
              ("simple_identifier", "identifier"):
                  :234 (+"type_identifier"), :268, :304, :307, :336, :483, :500,
                  :504, :523, :538, :556, :564, :576, :620, :646, :657, :680
                  :670 _extract_usertype_name accepts ("type_identifier", "identifier")
              Call machinery:
                  :180 t == "call_expression" (visit); :536, :559 (type inference)
                  :569/:613/:659 navigation_expression
                  :661 child.type == "call_suffix"
                  :554 this_expression
              Other node types: :141 function_declaration, :159 property_declaration,
              :220 modifiers, :302/:498 variable_declaration,
              :477 function_value_parameters, :479 ("parameter", "function_value_parameter")
  status:     DONE
  verify:     grep -n node-type literals (output above, pasted from session grep) PASS
              uv run --extra dev --extra test pytest tests -q -k kotlin
                -> 13 passed, 2118 deselected in 4.45s   PASS
  gap:        none
```

```
item 3: "Parse-error handling (ERROR-node checks, parse-failure counters, health checks)"
  evidence:   NO tree-sitter ERROR-node checks exist. Session grep
              `grep -rn "has_error" --include="*.py" src/` -> zero hits.
              Session grep `'"ERROR"'` in tests/ -> only
              tests/test_logging_config.py:98  os.environ["CAIRN_LOG_LEVEL"] = "ERROR"
              Parse-failure counters exist but count Python EXCEPTIONS, not
              grammar ERROR nodes:
              src/cairn/graph/builder.py:249  parse_errors = sum(1 for r in parsed_results if r[6] is not None)
              src/cairn/graph/builder.py:250  emit("parse_done", parsed=len(parsed_results), errors=parse_errors)
              src/cairn/graph/schema.py:141    CREATE TABLE IF NOT EXISTS parse_errors (
              Surfaced by: src/cairn/cli/system.py:162 (metrics builds table reads
              parse_errors column), :450-453 (status reads parse_errors table).
              Doctor checks (src/cairn/cli/system.py + tests/test_doctor.py:88
              test_eight_checks_always_emitted): schema, embeddings, ann,
              freshness, tasks — NO grammar/parse-tree check.
              What WOULD notice a grammar regression today: only snapshot
              equality — tests/test_golden_parsers.py:8
              test_parser_output_matches_golden (exact symbol/edge JSON vs
              tests/fixtures/golden/kotlin/expected.json) and
              tests/test_kotlin_operator_invoke.py:228 test_call_shape_table.
  status:     PARTIAL
  verify:     grep -rn "has_error" src/ -> no output (empty)  PASS (confirms absence)
              uv run --extra dev --extra test pytest tests/test_golden_parsers.py -q
                -> passed (within the 30-passed run below)  PASS
  gap:        no code anywhere inspects tree.root_node for ERROR/MISSING nodes;
              parse_errors tracks Python-level exceptions only, so a grammar
              that silently degrades shapes (ERROR nodes without exceptions)
              is caught only by golden/operator-invoke snapshots
```

```
item 4: "Build/packaging today (backend, ext-modules, wheel/release workflows, cibuildwheel, platform matrix)"
  evidence:   pyproject.toml:2-3
                  requires = ["setuptools>=61.0"]
                  build-backend = "setuptools.build_meta"
              Pure-python today: grep ext_modules/py-modules/ext-modules in
              pyproject.toml -> no hits; [tool.setuptools.packages.find]
              where = ["src"] (pyproject.toml:146-147); package-data only for
              agent_integration and dashboard templates/static (:149-154).
              .github/workflows/release.yml:43-44 comment:
                  # Matches `make dist`. uv build respects the setuptools backend in
                  # pyproject.toml and produces a pure-python py3-none-any wheel.
              release.yml:46 run: uv build   (job `build`, runs-on: ubuntu-latest only)
              release.yml jobs: build -> publish-pypi (Trusted Publishing,
              pypa/gh-action-pypi-publish) -> github-release (changelog section
              extraction via awk over CHANGELOG.md).
              .github/workflows/ci.yml:269-272 build job: python -m build,
              verify wheel imports (`python -c "import cairn; print('ok')"`),
              runs-on: ubuntu-latest.
              CI matrix (ci.yml:155) is PYTHON-version only:
                  python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]
              — no OS/arch matrix in either workflow.
              cibuildwheel: ABSENT from cairn's own config — session grep over
              *.toml/*.yml/*.yaml/*.cfg/Makefile hits only the VENDORED corpus
              benchmarks/datasource/t2/yarl/pyproject.toml:152 [tool.cibuildwheel]
              (third-party content, not cairn's build).
              Compiled code ships today only as EXTERNAL PyPI wheels:
              pyproject.toml:29-43 (tree-sitter==0.26.0 + 13 other grammar pins,
              sqlite-vec>=0.1.0 at :50).
  status:     TODO
  verify:     grep -rn "cibuildwheel\|cibw" pyproject.toml .github/ Makefile
                -> no output (empty; only vendored yarl corpus hits when
                   benchmarks/ included)  PASS (confirms absence)
  gap:        no in-tree compiled-extension machinery: no ext-modules in
              pyproject, no cibuildwheel, no setup.py, wheel is py3-none-any
              built on ubuntu only; FR-005's platform-wheel pipeline
              (macos arm64/x64, manylinux x86_64/aarch64, musllinux, Windows)
              does not exist
```

```
item 5: "Dependency declarations (every tree-sitter-kotlin occurrence)"
  evidence:   Complete repo-wide inventory (session greps over *.py *.toml *.md
              *.txt *.cfg *.yml *.yaml Makefile + separate uv.lock/.github/
              constraints/docs/benchmarks sweeps):
              pyproject.toml:30            "tree-sitter-kotlin==1.1.0",   (CORE deps, not an extra)
              uv.lock:215                  { name = "tree-sitter-kotlin" },          (dev extra resolution list)
              uv.lock:298                  { name = "tree-sitter-kotlin", specifier = "==1.1.0" },
              uv.lock package block        name = "tree-sitter-kotlin" / version = "1.1.0" + sdist + wheels
              NOTICE:26                    tree-sitter-go, tree-sitter-java, tree-sitter-javascript, tree-sitter-kotlin,   (MIT section)
              src/cairn/parsers/_registry.py:4    docstring "per-language wheels (tree-sitter-kotlin,"
              src/cairn/parsers/_registry.py:121  "kotlin": "tree_sitter_kotlin",
              tests/test_parser_audit_fixes.py:18   - F3 Kotlin class-body properties: tree-sitter-kotlin 1.1.0 emits
              tests/test_parser_audit_fixes.py:236  """tree-sitter-kotlin 1.1.0 emits ``identifier`` (not
              specs/kotlin-grammar-fwcd/research.md:17-48, spec.md:18-89    (this spec's own files)
              NOT present in: .github/ (workflows, PR template), docs/*.md
              (architecture.md mentions tree-sitter generically only),
              .pre-commit-config.yaml, Makefile, scripts/, benchmarks/ (outside
              vendored corpus), no constraints*.txt / requirements*.txt exist
              anywhere outside the vendored corpora.
  status:     DONE
  verify:     session grep output above (each line verbatim)  PASS
  gap:        none
```

```
item 6: "SCIP path independence for Kotlin (scip-java merge vs tree-sitter grammar)"
  evidence:   The merge is NOT grammar-independent — tree-sitter rows are the
              merge substrate:
              docs/configuration.md:148  "At build time, tree-sitter still parses those
                files (for modifiers, body, inheritance, parent scope that SCIP can't
                emit); SCIP's compiler-grade call/reference edges are then **merged**
                onto the tree-sitter rows (`source='merged'`)"
              src/cairn/graph/builder.py:410-413 comment:
                  # SCIP coexistence: if cairn.json declares a pre-built SCIP index for a
                  # language and that index file exists, tree-sitter STILL parses those files
              src/cairn/parsers/scip_importer.py:329
                  def _merge_scip_defs_into_tree_sitter(conn, scip_def_rows: list) -> int:
                  """Fold each SCIP definition symbol into its matching tree-sitter row."""
              scip_importer.py:20-23: "``INSERT OR IGNORE`` on ``files``/``symbols``
                keeps tree-sitter rows intact ... (the hybrid build skips tree-sitter for
                SCIP-covered languages, but ``import-scip`` against an already-built DB must"
                [quote continues past excerpt]
              Kotlin's indexer is scip-java:
              src/cairn/parsers/scip_indexers.py:64-68 _scip_java_cmd comment:
                  # scip-java is the canonical indexer for BOTH Java and Kotlin (the old
                  # scip-kotlin has been merged in and is no longer maintained)
              scip_indexers.py _KNOWN_INDEXERS: "kotlin": IndexerSpec(language="kotlin",
                tool="scip-java", ...)
              docs/scip.md:52  # Kotlin (scip-java — the canonical Java+Kotlin indexer; scip-kotlin is merged in)
              docs/scip.md:122 | `kotlin`     | `scip-java`       | same as Java — scip-kotlin is merged into scip-java
              Pure-SCIP revert mode exists when merge rate is 0
              (docs/configuration.md:148 "the build reverts that language to pure-SCIP").
              Tests: tests/test_build_scip_hybrid.py (22 kotlin-mention lines —
              hybrid merge behavior).
  status:     DONE
  verify:     uv run --extra dev --extra test pytest tests/test_build_scip_hybrid.py
                tests/test_golden_parsers.py tests/test_kotlin_operator_invoke.py
                tests/test_self_demo.py -q
                -> 30 passed in 23.08s   PASS
  gap:        none (interaction documented: swap of the tree-sitter grammar
              feeds the rows the scip-java merge joins against, so FR-007's
              merge path shares the tree-sitter kotlin parse)
```

```
item 7: "Kotlin test inventory"
  evidence:   `operator fun invoke` UseCase-idiom regression suite —
              tests/test_kotlin_operator_invoke.py, 8 tests:
                :91  test_operator_invoke_resolves_to_usecase_class
                :105 test_callers_and_impact_reach_the_usecase_class
                :127 test_two_viewmodels_both_reach_same_usecase
                :156 test_bare_local_function_and_constructor_calls_unchanged
                :172 test_lambda_typed_property_not_rewritten
                :192 test_genuine_method_call_not_rewritten
                :212 test_explicit_invoke_call_unchanged
                :228 test_call_shape_table
              Golden corpus test — tests/test_golden_parsers.py:8
              test_parser_output_matches_golden, parametrized over LANG_CONFIG
              (tests/fixtures/golden/regenerate.py:24  "kotlin": (KotlinParser, "sample.kt"));
              fixtures tests/fixtures/golden/kotlin/{sample.kt,expected.json}
              (sample.kt exercises interface, enum class, open class,
              primary-constructor val params, inheritance `: BaseEntity(name),
              Identifiable`, class-body properties, methods).
              Audit-fix Kotlin unit tests — tests/test_parser_audit_fixes.py:
                :234 class TestKotlinClassBodyProperties:
                :235   test_class_body_properties_emit_symbols
                :258   test_constructor_val_params_still_properties
                :307/:324/:352/:381 scip-kotlin merge tests ("scip-kotlin com example")
              Base-class conformance — tests/test_tree_sitter_parser_base.py
              (:58,:61,:76,:79,:102,:105 list KotlinParser among all parsers).
              Core-marked end-to-end — tests/test_core_smoke.py:54
              test_build_graph_resolves_usecase_bare_call (Kotlin bare
              `useCase(p)` resolves to the UseCase class through the full
              parser -> resolver -> schema pipeline).
              Self-demo — tests/test_self_demo.py:38
              test_self_demo_build_and_query, :84
              test_self_demo_resolution_invariant_holds (cairn-on-cairn; README.md:338
              "`pytest -m core` includes the **cairn-on-cairn self-demo**").
              Other kotlin-touching suites (grep -l): test_scip_indexers.py,
              test_scip_incremental.py, test_scip_importer.py,
              test_build_scip_hybrid.py, test_impact_test_labeling.py,
              test_dataflow_transitive_closure.py, and 15 more (22-file grep -l list).
              Standard commands:
              README.md:333-334  pytest -m core   /   pytest
              CONTRIBUTING.md:44-45
                pytest -m core -q      # fast smoke subset (<3s) — the inner dev loop
                pytest -q              # full suite (~60 test files) — the CI path
              tests/ contains 151 entries.
  status:     DONE
  verify:     uv run --extra dev --extra test pytest -m core -q
                -> 26 passed, 2105 deselected, 1 warning in 33.74s   PASS
              uv run --extra dev --extra test pytest tests -q -k kotlin
                -> 13 passed, 2118 deselected in 4.45s   PASS
  gap:        none
```

```
item 8: "Changelog [Unreleased] section shape"
  evidence:   CHANGELOG.md:13   ## [Unreleased]
              CHANGELOG.md:15   ### Added
              CHANGELOG.md:33   ### Fixed
              CHANGELOG.md:41   ### Changed
              Format: Keep a Changelog 1.1.0 (CHANGELOG.md:7), entries are
              multi-paragraph prose. Version bump at tag time via commitizen
              `cz bump` — pyproject.toml:215-224: version = "0.13.0",
              tag_format = "v$version", update_changelog_on_bump = false,
              version_files = pyproject.toml:version +
              src/cairn/__init__.py:__version__. Release workflow consumes the
              version section (release.yml:104-114 awk extraction).
              No kotlin-grammar entries exist yet under [Unreleased].
  status:     DONE
  verify:     sed -n '1,45p' CHANGELOG.md (session output: headings quoted above)  PASS
  gap:        none
```

## Supporting evidence
- Registry machinery for a grammar swap: `_load_language_module` mapping
  (_registry.py:119-138) is the single seam; `_SPECIAL_LOADERS`
  (_registry.py:43-52) is the existing precedent for a non-`language()`
  loader; plugin entry-point group `cairn.parsers.v1` (_registry.py:116)
  is an alternative registration seam.
- KotlinParser constructor binds the registry parser:
  kotlin.py:41 `self._parser = _get_ts_parser("kotlin")`.
- Runtime observed locally: CPython 3.11.16 (uv venv), tree_sitter 0.26.0,
  tree-sitter-kotlin 1.1.0, cairn 0.13.0.
- Wheel platform coverage of the current PyPI tree-sitter-kotlin 1.1.0
  (from uv.lock block): cp39-abi3 macosx_10_9_x86_64, macosx_11_0_arm64,
  manylinux_2_17_aarch64 (excerpt; list continues).
- CHANGELOG entries for the swap land under [Unreleased] → Added/Changed
  as per item 8; version bump happens only at tag time (`cz bump`,
  release.yml tag-push trigger `on: push: tags: ["v*"]`).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
