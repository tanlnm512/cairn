# Research: remove-scip-exact-rate

**Agent**: researcher (wave 1) · **Date**: 2026-09-11 · **Baseline**: `dc9882b224e783ab6bda25253ec049473fbf6e93` (`main`)
**Scope**: external grounding for RQ1–RQ4. Code ground truth lives in survey.md; nothing here overrides it.

Evidence marked **[pinned]** was produced in this session against the exact grammar versions cairn pins (`pyproject.toml:29-42`; verified installed in `.venv/lib/python3.11/site-packages/`). Evidence marked **[repo@tag]** came from the grammar repository's `src/node-types.json` at the pinned tag fetched this session.

---

## RQ1 — Syntax-only techniques that convert ambiguous references to single-definition bindings

### F1.1 Stack graphs (GitHub) — declarative scope rules over tree-sitter CSTs
- **source**: https://arxiv.org/abs/2211.01224 (Creager & van Antwerpen, 2022) · https://github.blog/open-source/introducing-stack-graphs/ · https://github.com/github/stack-graphs
- **claim**: Stack graphs encode name binding as a graph where "paths represent valid name bindings"; resolution is path-finding over per-file subgraphs; construction is "a purely syntactic analysis of the program's source code … without any per-package configuration, and without having to invoke an arbitrary, untrusted, package-specific build process" (abstract). Files are indexed incrementally with "no knowledge of, or visibility into, any other file in the program" — cross-file binding is stitched later.
- **relevance**: RQ1, FR-006/FR-008 — the ceiling of what syntax-only binding can do: scope-aware, shadowing-correct resolution; but it is a whole second resolution engine, not a heuristic on cairn's existing tiers.
- **confidence**: high (paper + repo + blog).

### F1.2 Stack graphs cost & current status — archived
- **source**: https://github.com/github/stack-graphs (README, fetched this session)
- **claim**: verbatim: "**NOTE:** _This repository is no longer supported or updated by GitHub. If you wish to continue to develop this code yourself, we recommend you fork it._" The repo shipped rules for only four languages (language packages present: `tree-sitter-stack-graphs-java`, `-javascript`, `-python`, `-typescript`). The Python rules had "been running in production since November 2021, analyzing every commit to every public and private Python repository hosted on GitHub" (paper §1).
- **relevance**: RQ1 cost side, spec Out-of-scope ("any new runtime dependency for name binding (stack-graphs-grade) unless a D-### justifies it per CONSTITUTION C-03") — upstream is dead; adopting it means forking 4 languages' rules and authoring the other 10 from zero.
- **confidence**: high (repo README verbatim).

### F1.3 Universal-ctags — field/kind/role enrichment of a tags index
- **source**: https://docs.ctags.io/en/latest/man/ctags.1.html · https://docs.ctags.io/en/latest/output-tags.html · PR https://github.com/universal-ctags/ctags/pull/4334
- **claim**: ctags attaches structured fields to each tag: `receiver` (Go: "Go: add a new field, 'receiver' to 'func' kind", PR #4334), `signature` ("a language-dependent representation of the signature of a routine (e.g. prototype or parameter list)"), `arity` ("Number of arguments for a function tag"), plus `roles` for reference tags ("kind field identifies the what … roles field identifies the how of a referenced language object") and the `qualified` extra ("tags a language object with a class-qualified or scope-qualified name"). These fields are exactly the disambiguation inputs; ctags itself does no cross-file resolution.
- **relevance**: RQ1, FR-006 — the cheap end: enrich emitted entities with receiver/signature/arity/qualified-name, resolve in a separate join step. Same shape as cairn's parser-signal + resolver-core split.
- **confidence**: high (official man pages).

### F1.4 Sourcegraph search-based code intelligence — heuristics over ctags + tree-sitter
- **source**: https://sourcegraph.com/docs/code-navigation/search-based-code-navigation (fetched this session) · https://sourcegraph.com/blog/announcing-scip
- **claim**: search-based navigation "performs a symbol search" for jump-to-def and "also filters results by file extension and by imports at the top of the file for some languages"; it is "used as a fallback when precise navigation is not available" and precise (LSIF/SCIP) results are always ranked first ("only after all of the precise results have been displayed"). Documented heuristics are: file extension filter + import filter — no alias tracking, no arity.
- **relevance**: RQ1 — documents the industry floor: even with precise indexes available, the syntax-only tier is kept as universal baseline (cairn post-SCIP position), and its documented precision levers are exactly import-filtering and per-file scoping.
- **confidence**: high (official docs).

### F1.5 Which reports the largest precision-safe gains
- **claim**: no source publishes a comparable "% ambiguous → exact" number for any of these systems. Qualitatively: stack graphs (F1.1) claims full shadowing/visibility correctness and is the only one documented to disambiguate same-name symbols reliably ("precise code navigation can give more accurate results, especially when a repository contains multiple methods or functions with the same name" — GitHub code-nav docs, via blog); ctags-field enrichment (F1.3) and Sourcegraph heuristics (F1.4) convert no ambiguous reference on their own — they make a downstream join *selective enough* to be safe.
- **relevance**: RQ1, FR-005 — cairn's exact-rate target is reachable via the F1.3/F1.4 pattern (signal enrichment + selective resolution); the F1.1 pattern is out of scope by constitution.
- **confidence**: medium (absence of published metrics is itself the finding; qualitative ordering is from official docs).

## RQ2 — Binding-relevant signals in the pinned tree-sitter grammars (14 languages)

Pin baseline (`pyproject.toml:29-42`): c 0.23.4 · cpp 0.23.4 · c-sharp 0.23.1 · dart 0.1.0 · go 0.25.0 · java 0.23.5 · javascript 0.23.0 · objc 3.0.2 · php 0.24.1 · python 0.23.6 · ruby 0.23.1 · swift 0.7.3 · typescript 0.23.2. **No `tree-sitter-kotlin` dependency is pinned** (see Gotchas G7).

### F2.1 Import statements & aliases — node shapes [pinned]/[repo@tag]
| lang | import node (verbatim parse / node-types) | alias signal | evidence |
|---|---|---|---|
| python | `import_from_statement module_name: (dotted_name) name: (aliased_import name: (dotted_name) alias: (identifier))` | field `alias` on `aliased_import`; `import_statement` also takes `aliased_import` | [pinned] parse of `from util import helper as h`; node-types v0.23.6 |
| javascript | `import_specifier` fields `{alias: [identifier], name: [identifier,string]}`; `import_statement` → `import_clause` → `named_imports`/`namespace_import` | field `alias` on `import_specifier` | [repo@tag] v0.23.0 node-types |
| typescript | identical to javascript (`import_specifier` `{name, alias}`) plus `import_require_clause {source}` | field `alias` | [repo@tag] v0.23.2/typescript node-types |
| go | `import_spec` fields `{name: [blank_identifier, dot, package_identifier], path: [interpreted_string_literal, raw_string_literal]}` | field `name` = named import / dot / blank | [repo@tag] v0.25.0 node-types |
| java | `import_declaration` children `[asterisk, identifier, scoped_identifier]`; `scoped_identifier` fields `{name, scope}` | no alias in Java; wildcard = `asterisk` child | [repo@tag] v0.23.5 node-types |
| php | `namespace_use_declaration (namespace_use_clause (qualified_name prefix: (namespace_name) (name)) alias: (name))` | field `alias` on `namespace_use_clause` | [pinned] parse of `use Foo\Bar as B;`; node-types v0.24.1 |
| csharp | `using_directive name: (identifier) (qualified_name qualifier: (identifier) name: (identifier))` for `using Foo = Bar.Baz;` — **no `name_equals` node at v0.23.1** (grep count 0 in node-types.json) | alias = `name` field when a `qualified_name` child is present; `using System.X;` has no such child → structural check, not a field | [pinned] parse + grep, v0.23.1 |
| swift | `import_declaration (identifier (simple_identifier))` — **no fields at 0.7.3** | none (positional children only) | [pinned] parse of `import Foundation`; node-types 0.7.3 |
| dart | `import_specification` children `[combinator, configurable_uri, identifier, uri]` — **no fields**; parse: `(import_specification (configurable_uri (uri (string_literal))) (identifier))` for `import 'x' as a;` | alias = trailing `identifier` child (positional) | [repo@tag] master node-types (v0.1.0 tag ships no node-types.json — see G6) + [pinned] parse of `import 'package:a/a.dart' as a;` |
| c/cpp/objc | `preproc_include` field `{path: [call_expression, identifier, string_literal, system_lib_string]}`; objc adds field `directive: ['#import','#include']` | n/a (include, not import) | [repo@tag] v0.23.4 / objc 3.0.2 node-types |
| ruby | no import node; nesting via `module name: (scope_resolution scope: (constant) name: (constant))` for `module A::B` | n/a — module path is `scope_resolution` chain | [pinned] parse |

- **relevance**: RQ2, FR-006/FR-007 — alias extraction is field-labeled and stable in python/js/ts/go/php; positional-only in swift/dart; structural in csharp.
- **confidence**: high (verbatim parses at pinned versions + repo node-types at pinned tags).

### F2.2 Receiver / qualified call expressions — node shapes [pinned]/[repo@tag]
| lang | call shape (verbatim) | receiver/qualifier signal |
|---|---|---|
| python | `(call function: (identifier) arguments: (argument_list))`; `attribute {attribute, object}` | field `object` on `attribute` for `obj.m` |
| java | `method_invocation` fields `{name: [identifier], object: [primary_expression, super], type_arguments}`; `field_access` fields `{field, object}` | field `object` |
| javascript/typescript | `call_expression {function, arguments}`; `member_expression {object, property: property_identifier, optional_chain}` | field `object` + `property` |
| go | `call_expression {function, arguments, type_arguments}`; `selector_expression {field: field_identifier, operand}`; `qualified_type {name, package: package_identifier}` | fields `operand`/`package` |
| ruby | `(call receiver: (scope_resolution scope: (constant) name: (constant)) method: (identifier))` | field `receiver` (any primary) + `method` |
| php | `member_call_expression {object, name, arguments}`; `scoped_call_expression {scope, name, arguments}` (parse: `B::baz()` → `scoped_call_expression scope: (name)`) | fields `object` / `scope` |
| csharp | `invocation_expression {function, arguments}`; `member_access_expression {expression, name}`; `qualified_name {name, qualifier}` | fields `expression`/`qualifier` |
| cpp | `qualified_identifier` fields `{name, scope: [namespace_identifier, …]}`; `call_expression {function, arguments}` | field `scope` on `qualified_identifier` |
| c | `call_expression {function: [expression], arguments}` | none (plain function field) |
| objc | `message_expression` fields `{method: [identifier], receiver: [expression, generic_specifier]}` | field `receiver` (methods are `[recv sel:...]`) |
| swift | `foo.bar()` → `(call_expression (navigation_expression target: (simple_identifier) suffix: (navigation_suffix suffix: (simple_identifier))) (call_suffix (value_arguments)))` — **`call_expression` has no fields at 0.7.3** | receiver = `navigation_expression`'s field `target` one level down |
| dart | `a.f()` → `(identifier) (selector (unconditional_assignable_selector (identifier))) (selector (argument_part))` — no fields on `selector` | positional: prefix identifier chain is the receiver |

- **relevance**: RQ2, RQ4, FR-006 — receiver-typed disambiguation (`obj.m()` vs bare `m()`) is directly readable from fields in 11/13 pinned grammars; swift/dart need positional child walks.
- **confidence**: high.

### F2.3 Scope blocks
- **source**: tree-sitter grammar node-types (all pinned repos above); stack-graphs scope rules https://github.com/github/stack-graphs/blob/main/languages/ (test corpora per scope construct)
- **claim**: every pinned grammar exposes block/function/class scope containers as named nodes (e.g. `block_statement`, `method_declaration`, `class_declaration`, `module`); the stack-graphs Java test tree (`test/decl/`, `test/expression/`, `test/imports_and_exports/` — incl. `duplicate_type_identifier.java`, `method_overriding.java`) is a ready-made catalog of the scope cases a syntax-only resolver is expected to survive.
- **relevance**: RQ2, FR-008 — enclosure/scope-distance tiers map 1:1 onto named scope nodes; the stack-graphs corpora are the checklist.
- **confidence**: high.

### RQ2 gotchas (grammar-version specifics)
- **G1 swift 0.7.3**: `call_expression` and `import_declaration` expose **no field labels** (node-types verbatim: `{"t":"call_expression","f":{}}`, `{"t":"import_declaration","f":{}}`); receiver lives on nested `navigation_expression {target, suffix}`. Any resolver reading `call_expression.function` fails silently on Swift.
- **G2 csharp 0.23.1**: no `name_equals` node (grep 0); using-alias must be detected structurally (`using_directive` with `qualified_name` child ⇒ the `name` field is the alias).
- **G3 php 0.24.1**: entry point is `language_php()` (not `language()`) — binding code importing it generically breaks (observed this session).
- **G4 ruby 0.23.1**: method calls are a single `call` node with `receiver`+`method` fields (not a member-expression wrapper); `alias` node has fields `{alias, name}` for `alias_method`-style aliasing.
- **G5 typescript monorepo**: node-types live at `typescript/src/` (and `tsx/src/`) — different subpath from every other grammar.
- **G6 dart 0.1.0**: the PyPI pin is ancient; its git tag `v0.1.0` ships no `src/node-types.json` (404 this session), so its documented node shapes only exist in the sdist/wheel build; master has diverged (now has `dot_shorthand`, `constructor_tearoff` nodes) — evidence above is from the [pinned] parse, the strongest available.
- **G7 kotlin**: `src/cairn/parsers/kotlin.py` exists but **no tree-sitter-kotlin pin** in `pyproject.toml` (grep `kotlin` → no match, this session). A maintained grammar exists: `tree-sitter-kotlin==1.1.0`, https://github.com/tree-sitter-grammars/tree-sitter-kotlin (PyPI, fetched this session). Whether kotlin currently parses at all is survey.md's call — flagged to orchestrator.
- **G8 (general)**: tree-sitter grammars do not promise field stability across minor versions (fields are regenerated from grammar.js); the pins above are the contract, so resolver queries should be pinned to the same versions — this is exactly why G1/G2 differ from older blog-era node names.

## RQ3 — Import-alias / re-export handling without a compiler

### F3.1 Stack-graphs approach — rewrite imports into module-file paths
- **source**: https://docs.rs/tree-sitter-stack-graphs (graph-DSL docs) · https://github.com/github/stack-graphs/tree/main/languages/tree-sitter-stack-graphs-python (`src/stack-graphs.tsg`)
- **claim**: the documented rule pattern matches the import node and normalizes it against the file path — verbatim example from the docs: `global FILE_PATH (import name:(_)@name)@import { … let dir = (path-dir FILE_PATH) let mod_name = (path-normalize (path-join dir (Source-text @name…)))` — i.e. import names are resolved by path arithmetic, then the imported file's exported definitions are attached to the importing scope. Python has run this in production at GitHub scale since Nov 2021 (paper §1).
- **relevance**: RQ3, FR-006 — the documented syntax-only mechanism for `from x import y as z`: map `z → (module x, exported name y)` at index time, then bind references to `z` against `y`'s definition via the module path join.
- **confidence**: high (official docs + production claim).

### F3.2 Known failure modes of F3.1
- **source**: https://github.com/github/stack-graphs/issues/430 · languages/tree-sitter-stack-graphs-python/CHANGELOG.md
- **claim**: real-world Python module resolution still broke on path setups ("Python module resolution not working via tree-sitter-stack-graphs-python cli", issue #430, Apr 2024) and needed a root-paths fix ("Added support for root paths. This fixes import problems when indexing using absolute directory paths", CHANGELOG). Syntax-only import handling cannot see package `__init__` aggregation, conditional re-exports, or dynamic imports — the stack-graphs corpora contain no test for re-export chains.
- **relevance**: RQ3, FR-009 — import-alias conversion is safe; *transitive* re-export chains (TS barrels, `__init__.py` stars) are the documented weak spot: one extra hop multiplies misbinding risk, so abstention there is the documented behavior, not a cairn-specific limitation.
- **confidence**: high (issues + changelog).

### F3.3 Sourcegraph's approach — filter, don't bind
- **source**: https://sourcegraph.com/docs/code-navigation/search-based-code-navigation
- **claim**: "filters results by file extension and by imports at the top of the file for some languages" — imports are used only to *prune* candidate definitions (must be imported to be considered), never to rename-bind; aliases are not tracked (no such claim anywhere in the doc).
- **relevance**: RQ3, FR-008 — the conservative alternative: alias-aware *filtering* (an edge to symbol `y` may resolve only in files that import `x as anything`) raises exactness without claiming the alias equals the definition.
- **confidence**: high (official docs).

### F3.4 Language-specific alias semantics exposed by grammars (compiled from F2.1)
- **source**: pinned parses/node-types in F2.1.
- **claim**: python (`from x import y as z`), php (`use Foo\Bar as B`), js/ts (`import {y as z}`), go (`import qux "path"`), csharp (`using Foo = Bar.Baz;`) all carry the alias on a dedicated node/field at cairn's pinned versions; kotlin's `import a.b.C as D` has the same shape in the maintained grammar (tree-sitter-kotlin 1.1.0, not pinned in cairn — G7); ruby has no imports (module nesting = `scope_resolution`, F2.1); js/ts re-exports (`export {y} from './x'`, `export *`) reuse `import`-side nodes but with export_ prefixes — no external indexer documents binding through them without a compiler (F3.2's gap).
- **relevance**: RQ3, FR-007, AC6 — direct single-hop alias conversion is grammar-supported on the pinned versions for 6+ languages; the survey names which edge shapes cairn currently leaves ambiguous.
- **confidence**: high for grammar shapes; medium for "no indexer documents re-export binding" (absence claim).

## RQ4 — Same-name / overload disambiguation heuristics with documented safety

### F4.1 Receiver-typing — documented in ctags (Go `receiver` field) and stack-graphs (type-directed lookup)
- **source**: https://github.com/universal-ctags/ctags/pull/4334 · https://arxiv.org/abs/2211.01224 · https://github.com/github/stack-graphs/issues/275
- **claim**: ctags added a dedicated `receiver` field to Go `func` tags so method tags can be disambiguated by receiver — a shipped, field-level commitment to receiver-as-key. Stack graphs handle "type-directed name lookups (which require 'pausing' the current lookup to resolve another name)" via the lookup stack (paper abstract) — i.e. receiver-object-first resolution is the documented precise mechanism.
- **relevance**: RQ4, FR-006 — receiver match as an exactness tier has two independent precedents; with F2.2's fields, `obj.m()` can bind to the `m` whose defining class matches the receiver's inferred class, else abstain.
- **confidence**: high.

### F4.2 Arity / signature — documented in ctags fields
- **source**: https://docs.ctags.io/en/latest/man/ctags.1.html · https://github.com/universal-ctags/ctags/blob/master/old-docs/website/FORMAT
- **claim**: ctags documents `arity` ("Number of arguments for a function tag") and `signature` ("language-dependent representation of the signature of a routine … presently supported only for C-based languages and does not include the return type") as tag fields — arity is a first-class documented disambiguator; signature only for C-family.
- **relevance**: RQ4, FR-006 — arity from call `argument_list` length vs definition parameter count: where overloads differ in arity, unique-arity match is a documented safe split; equal-arity overloads (C++/Java/TS common) must stay ambiguous.
- **confidence**: high (fields documented); medium (ctags documents the *field*, not a measured safety study — no such study exists in any source found).

### F4.3 Scope/shadowing enclosure — stack-graphs correctness boundary
- **source**: https://github.com/github/stack-graphs/issues/275
- **claim**: verbatim findings from the issue: "disambiguation is only applied on complete paths, which end in a definition with an empty symbol stack"; consequence shown: an inner `x` shadowing an outer `x` resolves correctly, but the `f` in `x.f` can wrongly resolve "to the field of the outer definition" — even the strongest syntax-only engine documents false-binding modes when receiver context and lexical scope interact.
- **relevance**: RQ4, FR-009 — enclosure/scope-distance tiers are correct for bare references, but *combining* receiver reasoning with shadowing is where false-exact creeps in; cairn's abstain-when-uncertain contract mirrors the documented failure envelope.
- **confidence**: high (maintainer-acknowledged issue).

### F4.4 Same-name filtering — GitHub precise-vs-search statement
- **source**: https://github.blog/open-source/introducing-stack-graphs/ (and GitHub code-navigation docs it links)
- **claim**: precise (scope-aware) navigation "can give more accurate results, especially when a repository contains multiple methods or functions with the same name" — the documented delta between scope-unaware search and scope-aware binding is precisely the same-name population; conversely no source documents call-site *line proximity* as a safe disambiguator (nobody ships it).
- **relevance**: RQ4, FR-005 — the convertible population is same-name definitions disambiguated by scope/receiver/arity; line-proximity heuristics are unsupported by any found source → treat as out.
- **confidence**: high on the positive claim; medium on the absence claim.

---

## Options summary

1. **Exact-rate mechanism** → (a) stack-graphs-grade scope engine — highest precision, upstream archived Sep 2025, only 4 languages ever had rules, new runtime dep (constitution-blocked without D-###); (b) ctags-style signal enrichment (receiver/signature/arity/alias fields) + selective resolver join — no new dep, documented field semantics, gains limited to what each grammar exposes (F2.1/F2.2); (c) Sourcegraph-style import/extension filtering only — cheapest, converts nothing by itself, prunes candidates.
2. **Import-alias binding depth** → (a) full module-path join à la stack-graphs (bind alias through to the exported definition) — strong but documented to break on path/package edge cases; (b) single-hop alias map (`z → x.y`) with abstention on re-export chains — grammar-supported on pinned versions for python/js/ts/go/php/csharp, failure modes documented and avoidable; (c) alias-aware filtering only (Sourcegraph) — safe, smallest gain.
3. **Overload/same-name disambiguation tiers** → receiver-type match (documented in ctags + stack-graphs lookup-stack) and arity match (ctags `arity`) both have precedent; scope/enclosure distance is safe for bare references; receiver×shadowing interaction is the documented false-binding zone (stack-graphs #275) → keep FR-009 abstention there; line proximity has zero documented support.
4. **Kotlin coverage (FR-007)** → pin `tree-sitter-kotlin==1.1.0` (maintained, tree-sitter-grammars) vs leave kotlin degrading to current behavior — but survey must first establish whether kotlin parses at all today given no pin exists (G7).

**Gaps (no credible source found)**: published % conversion rates for ambiguous→exact under any syntax-only technique (F1.5); any documented safe heuristic for equal-arity overload splits; any syntax-only tool documenting binding through TS re-export chains / python `__init__` star re-exports.
