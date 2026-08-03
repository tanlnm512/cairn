# Codegraph Skill Evals

These are scenario specs: each file describes a situation an agent using this
skill will hit, what the tool actually returns, the behavior that follows the
Golden Rules, and the specific wrong-but-plausible behavior it guards against.
Each spec also carries a machine-readable YAML frontmatter block (delimited
by `---`) that declares its `expected_calls` and `wrong_calls`, so they are
not purely prose anymore.

There is now a **structural validation runner** wired to them. It does NOT
run an agent or grade behavior -- it validates the frontmatter itself:

- run `make evals` (or `python3 scripts/run_skill_evals.py` directly)
- it checks that every spec has the required frontmatter keys
  (`id`, `rule`, `title`, `scenario`, `expected_calls`, `wrong_calls`);
- it checks that each `expected_calls`/`wrong_calls` entry has its required
  fields (`tool`+`reason`, `tool`+`why`);
- it resolves every referenced `tool` against the real registered surfaces --
  MCP tools (`@mcp.tool()` in `src/codegraph/mcp_server/tools_*.py`), `cg` CLI commands
  (scraped from `src/codegraph/cli/*.py`), and shipped scripts under `scripts/` -- and
  fails if a spec references a tool/command that was renamed or removed;
- it exits non-zero if any spec fails, so it is safe to put on a CI gate.

What it does **not** do (still manual): grade an actual agent transcript
against the expected behavior. Use the prose body of each spec for that.
The runner's value is catching drift between a spec and the codebase; the
behavioral pass/fail remains a human audit. Use the specs to:

- Sanity-check an agent transcript against expected behavior when auditing
  codegraph-explorer/knowledge-steward runs
- Grade a change to SKILL.md or the agent prompts: if the new wording would
  make an agent behave *differently* on one of these scenarios, that's a
  regression worth catching before shipping
- Onboard a new agent definition (Cursor subagent, Droid, ZCode) onto the
  same expected behavior, since the prompt text differs slightly per client

| File | Rule | Failure mode it guards against |
|------|------|--------------------------------|
| `rule06-impact-common-name.md` | Rule 6 | Reporting a name-collision-inflated `impact_analysis` count as real blast radius |
| `rule01-ambiguous-name.md` | Rule 1 | Treating a silent empty `[]` from `get_callers` as "no callers" instead of "unresolved name" |
| `rule08-precise-then-fuzzy.md` | Rule 8 | Starting with `fuzzy=True` and treating candidate noise as ground truth |
| `rule08b-empty-precise-gave-up.md` | Rule 8 | Reporting "no callers" after precise comes up empty for a symbol defined outside the indexed workspace, without retrying `fuzzy=True` (distinct from rule01: `search_symbols` can't help here since the symbol has no indexed definition to disambiguate) |
| `rule09-impact-plus-cross-repo.md` | Rule 9 | Calling `impact_analysis` alone for a shared-library symbol and reporting the within-repo count as the complete blast radius, missing cross-repo consumers |
| `rule10-mutations-via-cli.md` | Rule 10 | Attempting a graph rebuild, dataflow build, or memory purge via MCP (read-only for L1) instead of the CLI, and reporting the mutation as done |
