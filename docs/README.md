# cairn documentation

Start with the [README](../README.md) for the product pitch, install, and
measured results. This index maps the doc set by what you're trying to do.

## Start here

| Doc | What it covers |
|-----|----------------|
| [quickstart.md](quickstart.md) | First install to first query, step by step |
| [configuration.md](configuration.md) | `cairn.json`, env vars, extras, `~/.cairn` layout |
| [cli-reference.md](cli-reference.md) | Every `cairn` command and flag |
| [mcp-tools.md](mcp-tools.md) | The 27 MCP tools — shapes, examples, escalation ladder |

## How it works

| Doc | What it covers |
|-----|----------------|
| [architecture-overview.md](architecture-overview.md) | The one-page mental model |
| [architecture.md](architecture.md) | Full design: layers, resolution model, storage |
| [query-flow.md](query-flow.md) | What happens on a query, end to end (with [diagrams](diagrams/)) |
| [methodology-precise-vs-fuzzy.md](methodology-precise-vs-fuzzy.md) | Why precise edges are ground truth; the false-positive measurement |
| [scip.md](scip.md) | SCIP/tree-sitter coexistence indexing for compiler-grade edges |
| [examples/resolution-walkthrough.md](examples/resolution-walkthrough.md) | A worked precise-vs-fuzzy example on real code |

## Evidence

| Doc | What it covers |
|-----|----------------|
| [benchmarks.md](benchmarks.md) | Perf, scaling, warm-time, agent-effort, and retrieval-quality tables (generated from committed artifacts between sentinels — don't hand-edit) |

## Operate

| Doc | What it covers |
|-----|----------------|
| [BUGS.md](BUGS.md) | Known-issues registry (index table + TL;DR per entry) |
| [audit-checklist.md](audit-checklist.md) | Periodic area-driven audit procedure |
| [release-checklist.md](release-checklist.md) | Cutting a release, step by step |

## Contribute

| Doc | What it covers |
|-----|----------------|
| [contribution-workflow.md](contribution-workflow.md) | The mandatory branch → pre-commit → PR → CI workflow |
| [review-checklist.md](review-checklist.md) | The per-PR review gate (blast radius, layering, hygiene) |
| [postmortems/](postmortems/) | Incident write-ups and their lessons |
