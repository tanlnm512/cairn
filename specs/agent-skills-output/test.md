# Test Cases: agent-skills-output

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-16

Black-box, business-language verification traced to requirements. Case text
stays in product terms (CLI verbs, the workspace's skills home, printed
output); exact runnable commands live only in the pass conditions.

## Runner conventions

- The measuring binary is the repository venv's CLI, which resolves this
  tree: commands assign `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn`
  first. The `cairn` found on PATH may be a different install and is never
  used.
- Every case builds a throwaway fixture workspace (`mktemp -d`, `git init`,
  a two-file package `pkg_a` whose `zeta_hub` is the module's call hub while
  `alpha_leaf` nothing calls, plus a root file `sub_mod.py` defining
  `beta_one`) and pins an isolated store with a fresh `CAIRN_HOME` — never
  the live store. Fixture build measures ~1.5 s, so every case runs in
  seconds, far under the audit's 120 s cap.
- Slug normalization is not part of the contract: path assertions use shell
  globs over `.agents/skills/cairn-*` and pin only the `cairn-` prefix plus
  the selector-derived stem.
- The generator does not exist on the baseline tree (survey: no skill
  surface in the CLI) — every case reds until the command ships. Where a
  case could otherwise vacuously green, a preceding control run (a valid
  generation that must succeed) orders the checks.
- After a command, `# → ...` states the expected observable; it is not part
  of the command.

## TC-001 — A module selector generates a skill at the workspace's default skills home
- **Story**: US1 · **Traces to**: FR-001, FR-006, AC1
- **Given** an indexed workspace and a module with indexed symbols
- **When** the generate command runs with that module's name as the selector
- **Then** a skill file is written under the workspace's default skills home, inside a `cairn-`-prefixed directory named for the module — not scattered in the workspace root.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc001.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n\ndef mid_one():\n    return zeta_hub()\n\ndef mid_two():\n    return zeta_hub()\n\ndef alpha_leaf():\n    return 2\n' > pkg_a/core.py && printf 'from pkg_a.core import zeta_hub\n' > pkg_a/__init__.py && printf 'def beta_one():\n    return 1\n' > sub_mod.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a >/dev/null && ls "$W"/.agents/skills/cairn-pkg*/SKILL.md >/dev/null && echo skill-written-at-default-home` # → the marker prints (glob pins the cairn- prefix and the module stem; exact slug spelling is not part of the contract)

## TC-002 — The generated skill carries loadable frontmatter: a name and a load-trigger description
- **Story**: US1 · **Traces to**: FR-001, FR-005, AC1
- **Given** a generated skill for a module
- **When** the skill file is opened
- **Then** it opens with a fenced metadata block whose keys are the skill's name and a description — and the description carries real load-trigger wording (when an agent should load this), not an empty value — followed by a non-empty body.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc002.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n\ndef mid_one():\n    return zeta_hub()\n\ndef mid_two():\n    return zeta_hub()\n\ndef alpha_leaf():\n    return 2\n' > pkg_a/core.py && printf 'from pkg_a.core import zeta_hub\n' > pkg_a/__init__.py && printf 'def beta_one():\n    return 1\n' > sub_mod.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a >/dev/null && S=$(ls "$W"/.agents/skills/cairn-pkg*/SKILL.md) && [ "$(head -1 "$S")" = "---" ] && VP=/Users/lnmtan/Projects/others/cairn/.venv/bin/python && $VP -c "import yaml,sys; t=open(sys.argv[1]).read(); fm=yaml.safe_load(t.split('---')[1]); d=fm['description']; assert isinstance(d,str) and len(d)>=20 and 'name' in fm" "$S" && BODY=$(awk '/^---/{c++; next} c>=2 && NF {print; exit}' "$S") && [ -n "$BODY" ] && echo frontmatter-loadable` # → the marker prints (frontmatter is checked with an order-agnostic YAML load: name present and description a real string of 20+ trigger-wording chars, whatever their order — the earlier awk extraction was key-order-fragile, exiting at whichever key came first and misreading a wrapped multi-line description as empty; the BODY probe stays line-based since it only needs any non-blank line after the closing fence)

## TC-003 — Generated layout matches the skill format clients already load
- **Story**: US1 · **Traces to**: FR-005
- **Given** a generated skill and the static skill package the installer already ships to every skill-compatible client
- **When** both files are compared structurally
- **Then** they share the same loadable shape: a fenced metadata block opening the file, carrying name and description keys, over a markdown body — so a client that loads the shipped skill today loads the generated one with no manual assembly.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc003.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n' > pkg_a/core.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a >/dev/null && S=$(ls "$W"/.agents/skills/cairn-pkg*/SKILL.md) && VP=/Users/lnmtan/Projects/others/cairn/.venv/bin/python && ST=$($VP -c "import cairn, pathlib; print(pathlib.Path(cairn.__file__).parent / 'agent_integration' / 'skill' / 'SKILL.md')") && [ "$(head -1 "$ST")" = "---" ] && [ "$(head -1 "$S")" = "---" ] && for F in "$ST" "$S"; do awk 'NR==1{next} /^---/{exit} {print}' "$F" | grep -q "^name:" && awk 'NR==1{next} /^---/{exit} {print}' "$F" | grep -q "^description:" || exit 1; done && echo layout-matches-distributed-skill` # → the marker prints (the reference file is the shipped static skill inside the installed package — the same artifact the installer merges into client skill directories)

## TC-004 — The body assembles the module's compass, its symbols, and relevant memory
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a workspace where the module has a generated navigation guide, indexed symbols, and one recorded memory tied to the module's file
- **When** the module's skill is generated
- **Then** the packaged skill carries all three: content from the module's navigation guide, the module's symbol names, and the recorded memory's content.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc004.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n\ndef mid_one():\n    return zeta_hub()\n\ndef mid_two():\n    return zeta_hub()\n\ndef alpha_leaf():\n    return 2\n' > pkg_a/core.py && printf 'from pkg_a.core import zeta_hub\n' > pkg_a/__init__.py && printf 'def beta_one():\n    return 1\n' > sub_mod.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B compass generate pkg_a >/dev/null && CAIRN_HOME=$H $B memory record pattern "qxzephyrline zeta-hub load contract" --body "zeta_hub callers depend on its return value. Why: packaged context must carry the module memory. How to apply: surface it when pkg_a context is loaded." --resource pkg_a/core.py >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a >/dev/null && D=$(ls -d "$W"/.agents/skills/cairn-pkg*) && grep -rq "What Does This Module Do?" "$D" && grep -rq zeta_hub "$D" && grep -rq qxzephyrline "$D" && echo compass-symbols-memory-assembled` # → the marker prints (recorded memories land in the recall-visible tribal tier, verified this session; the compass marker is that guide's top heading — an excerpt rendered without headings reds this and adjudicates; the greps sweep the whole skill directory since content may sit in reference files)

## TC-005 — A directory prefix selects everything under that directory and nothing outside it
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed workspace with one directory holding two symbols and a sibling file outside that directory holding a third
- **When** the generate command runs with the directory prefix as the selector
- **Then** the generated skill names the two inside symbols and never the outside one.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc005.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n\ndef alpha_leaf():\n    return 2\n' > pkg_a/core.py && printf 'def beta_one():\n    return 1\n' > sub_mod.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a/ >/dev/null && S=$(ls "$W"/.agents/skills/cairn-*/SKILL.md) && grep -q zeta_hub "$S" && grep -q alpha_leaf "$S" && ! grep -q beta_one "$S" && echo directory-prefix-selected-module-only` # → the marker prints (prefix form is `pkg_a/`; the glob tolerates slug normalization since the fresh fixture holds exactly one generated skill)

## TC-006 — An explicit symbol list selects exactly the named symbols
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed workspace with symbols across two modules
- **When** the generate command runs with an explicit list of two symbol names
- **Then** the generated skill names both listed symbols and never the unlisted one from the other module.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc006.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n\ndef alpha_leaf():\n    return 2\n' > pkg_a/core.py && printf 'def beta_one():\n    return 1\n' > sub_mod.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate zeta_hub alpha_leaf >/dev/null && D=$(ls -d "$W"/.agents/skills/cairn-*) && grep -rq zeta_hub "$D" && grep -rq alpha_leaf "$D" && ! grep -rq beta_one "$D" && echo explicit-symbol-list-selected` # → the marker prints (list form is space-separated symbol names per the command's help — another documented delimiter reds this and adjudicates the form, not the behavior)

## TC-007 — Symbols are ranked by structural centrality, not alphabet
- **Story**: US1 · **Traces to**: FR-002, AC1
- **Given** a module where the best-connected symbol is named to sort last alphabetically and an unconnected one sorts first
- **When** the module's skill is generated
- **Then** the connected symbol appears above the isolated one in the skill — the ordering follows how much depends on each symbol, not the alphabet.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc007.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n\ndef mid_one():\n    return zeta_hub()\n\ndef mid_two():\n    return zeta_hub()\n\ndef alpha_leaf():\n    return 2\n' > pkg_a/core.py && printf 'from pkg_a.core import zeta_hub\n' > pkg_a/__init__.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a >/dev/null && S=$(ls "$W"/.agents/skills/cairn-pkg*/SKILL.md) && Z=$(grep -bo zeta_hub "$S" | head -1 | cut -d: -f1) && A=$(grep -bo alpha_leaf "$S" | head -1 | cut -d: -f1) && [ -n "$Z" ] && [ -n "$A" ] && [ "$Z" -lt "$A" ] && echo hub-ranked-above-isolated` # → the marker prints (zeta_hub has other symbols depending on it — the fixture map reports it as the module hub — while alpha_leaf has zero dependents; the names reverse the alphabetical order so any non-structural sort fails this)

## TC-008 — A very large module yields a bounded top-K skill, not the whole module
- **Story**: US1 · **Traces to**: FR-002
- **Given** a module defining two hundred symbols
- **When** the module's skill is generated
- **Then** the skill lists a bounded subset — at least one symbol but strictly fewer than the module contains — because the contract is top-K by centrality, not an inventory dump.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc008.XXXXXX) && cd "$W" && git init -q . && mkdir -p big_mod && for i in $(seq 1 200); do printf 'def qsym_%03d():\n    return %d\n\n' "$i" "$i" >> big_mod/core.py; done && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate big_mod >/dev/null && S=$(ls "$W"/.agents/skills/cairn-big*/SKILL.md) && C=$(grep -oE "qsym_[0-9][0-9][0-9]" "$S" | sort -u | wc -l | tr -d " ") && echo "listed=$C of 200" && [ "$C" -ge 1 ] && [ "$C" -lt 200 ]` # → 1 <= listed < 200 (K is the generator's documented default; the contract pins "a bounded subset". Fixture is a directory module — a bare-name selector over a root file is exact-symbol semantics per TC-006, which would package the file's module symbol alone. Bounded corpus: 200 tiny functions index in seconds — the same assertion over a production-sized tree is the standing verify, kept out of the audit's per-case cap)

## TC-009 — The default path is deterministic and never needs a model (standing guard)
- **Story**: US1 · **Traces to**: FR-003, AC2
- **Given** an indexed workspace with all model credentials stripped from the environment
- **When** the same skill is generated twice, deleting the first result before the second run
- **Then** both runs succeed and produce byte-identical skills — a model in the default path would either fail without credentials or drift between runs, so identical bytes with no credentials is the guard that fails if an LLM ever creeps into the default path.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc009.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n\ndef alpha_leaf():\n    return 2\n' > pkg_a/core.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY CAIRN_HOME=$H $B skill generate pkg_a >/dev/null && S=$(ls "$W"/.agents/skills/cairn-pkg*/SKILL.md) && cp "$S" "$W/run1.md" && rm -rf "$W/.agents" && env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY CAIRN_HOME=$H $B skill generate pkg_a >/dev/null && cmp "$W/run1.md" "$S" && echo deterministic-without-model` # → the marker prints (byte-identical output across runs under credential-stripped env — the standing guard for the deterministic default)

## TC-010 — Model polish, where offered, is documented behind the task queue and the critic
- **Story**: US1 · **Traces to**: FR-003
- **Given** the generate command's help
- **When** its selector documentation and any polish option are read
- **Then** the help documents the three selector forms (module, directory prefix, symbol list) — the control that keeps this case red until the command ships — and where a polish option is offered at all, its wording names the task queue or the critic, never an inline model call from the command itself.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && O=$($B skill generate --help 2>&1) && echo "$O" | grep -qi module && echo "$O" | grep -qiE "prefix|directory" && echo "$O" | grep -qiE "symbol" && if echo "$O" | grep -qi polish; then echo "$O" | grep -qiE "task.?queue|critic"; else echo polish-not-offered; fi` # → exit 0 with the selector-form greps passing (control); the polish branch passes either when its wording names the task queue/critic or when no polish option ships, since the requirement is conditional (WHERE enabled)

## TC-011 — A selector naming an unknown symbol never writes that symbol (standing guard)
- **Story**: US2 · **Traces to**: FR-004, FR-002, AC1
- **Given** an indexed workspace and a generation request whose selector names one real symbol and one symbol that does not exist in the graph
- **When** generation runs
- **Then** no written skill anywhere in the skills home mentions the unknown symbol — excluded or rejected, never packaged — while the request's real symbol still packages (control).
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc011.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n' > pkg_a/core.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a >/dev/null 2>&1 && CAIRN_HOME=$H $B skill generate zeta_hub ghost_symbol_qq >/dev/null 2>&1; if grep -rq ghost_symbol_qq "$W"/.agents/skills 2>/dev/null; then echo GHOST-LEAKED; exit 1; else echo unknown-symbol-never-written; fi` # → the marker prints (the first generation is the control — without it a featureless baseline would vacuously green this case; the ghost sweep covers every file in the skills home)

## TC-012 — A request that cannot verify leaves no partial skill behind
- **Story**: US2 · **Traces to**: FR-004, AC1
- **Given** an indexed workspace with one already-generated skill
- **When** a generation request names only symbols that do not exist in the graph
- **Then** the skills home holds no new entry afterwards — verification happens before anything is written, so a rejected request leaves no empty or partial skill directory.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc012.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n' > pkg_a/core.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a >/dev/null && N1=$(ls -1 "$W"/.agents/skills | wc -l | tr -d " ") && CAIRN_HOME=$H $B skill generate ghost_qq ghost_zz >/dev/null 2>&1; N2=$(ls -1 "$W"/.agents/skills | wc -l | tr -d " ") && [ "$N2" -le "$N1" ] && echo no-partial-skill-written` # → the marker prints (entry count after the rejected request is at most the count before it)

## TC-013 — An explicit output override bypasses the default skills home
- **Story**: US1 · **Traces to**: FR-006
- **Given** an indexed workspace
- **When** generation runs with an explicit output directory
- **Then** the skill lands under that directory and the default skills home is not created at all.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc013.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n' > pkg_a/core.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a --output "$W/custom_out" >/dev/null && [ -f "$W/custom_out/SKILL.md" ] && [ ! -e "$W/.agents/skills" ] && grep -q zeta_hub "$W/custom_out/SKILL.md" && echo override-honored-default-untouched` # → the marker prints (the override form is `--output DIR` after the selector, per the command's help)

## TC-014 — An unknown module fails cleanly and writes nothing
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed workspace with one generated skill
- **When** generation runs with a module name that indexes nowhere in the workspace
- **Then** the command reports failure rather than emitting an empty skill, and the skills home holds no new entry.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc014.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n' > pkg_a/core.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a >/dev/null 2>&1 && N1=$(ls -1 "$W"/.agents/skills | wc -l | tr -d " ") && if CAIRN_HOME=$H $B skill generate no_such_mod_qq >/dev/null 2>&1; then echo UNEXPECTED-SUCCESS; exit 1; fi && N2=$(ls -1 "$W"/.agents/skills | wc -l | tr -d " ") && [ "$N2" = "$N1" ] && echo clean-failure-nothing-written` # → the marker prints (the valid generation first is the control; the unknown selector must exit non-zero and leave the entry count unchanged)

## TC-015 — An empty knowledge store still generates a usable skill
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed workspace with a symbol-bearing module but no navigation guide and no recorded memories
- **When** the module's skill is generated
- **Then** generation succeeds and the skill still names the module's symbols — the knowledge-derived sections are optional content, not preconditions.
- **Pass condition**: `B=/Users/lnmtan/Projects/others/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc015.XXXXXX) && cd "$W" && git init -q . && mkdir -p pkg_a && printf 'def zeta_hub():\n    return 1\n\ndef alpha_leaf():\n    return 2\n' > pkg_a/core.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B skill generate pkg_a >/dev/null && S=$(ls "$W"/.agents/skills/cairn-pkg*/SKILL.md) && grep -q zeta_hub "$S" && echo usable-skill-from-empty-knowledge` # → the marker prints (no compass generate, no memory record in the fixture — the graph alone must be enough to package)

## Coverage matrix
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-004, TC-005, TC-006, TC-014, TC-015 | auto |
| FR-002      | TC-007, TC-008, TC-011 | auto |
| FR-003      | TC-009, TC-010 | auto |
| FR-004      | TC-011, TC-012 | auto |
| FR-005      | TC-002, TC-003 | auto |
| FR-006      | TC-001, TC-013 | auto |

## Acceptance-criterion coverage
| Acceptance criterion | Test cases |
|----------------------|------------|
| US1 AC1 | TC-001, TC-002, TC-004, TC-007 |
| US1 AC2 | TC-009 |
| US2 AC1 | TC-011, TC-012 |

## Evidence anchors
| Survey item | Anchored by |
|-------------|------------|
| S1 (static skill ships today) | TC-003 (the format reference the generated skill must match) |
| S3 (every content input has a reader) | TC-004 (compass, symbols, memory all reach the body) |
| S4 (deterministic critic exists) | TC-011, TC-012 (verification gates every write) |
| S2 (per-client distribution paths) | no case — FR-006 defers per-client distribution of generated skills out of MVP scope |
