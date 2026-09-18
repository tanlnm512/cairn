# Survey: cross-repo-federation

**Created**: 2026-09-16 | **Baseline**: codex/docs-level-up-specs @ d18768c
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "A machine-wide workspace registry enumerates every store"
  evidence:   src/cairn/paths.py:35 `REGISTRY_FILE = CAIRN_HOME / "workspaces.json"`; :177 `_load_registry()` returns `{workspace_abs_path: key}`; :193 `def register_workspace(workspace: Path) -> StorePaths:` (called by `cairn init`, cli/core.py:36 `store = register_workspace(ws)`)
  status:     DONE
  verify:     grep -n "REGISTRY_FILE\|def register_workspace" src/cairn/paths.py
  gap:        nothing queries across registry entries; every command resolves one store

item S2: "cross_repo_deps is structural and within one store"
  evidence:   src/cairn/graph/cross_repo.py:56 `_load_namespaces()` (repo name → path map inside one DB); :125 `def cross_repo_deps(conn, repo)`; cli exposes `cairn deps <repo>` (cli/query.py `def deps(repo, db, as_json)`)
  status:     DONE
  verify:     grep -n "def cross_repo_deps\|def _load_namespaces" src/cairn/graph/cross_repo.py
  gap:        no semantic/retrieval query spans stores

item S3: "Rank fusion precedent for incomparable scores"
  evidence:   src/cairn/graph/fusion.py (RRF used by the hybrid retrieval stack; semantic.py documents `CAIRN_FUSION` BM25+vector reciprocal-rank fusion)
  status:     DONE
  verify:     grep -rn "RRF\|reciprocal" src/cairn/graph/fusion.py src/cairn/graph/semantic.py | head -5
  gap:        fusion operates within one store today

item S4: "`cairn ask` exists single-store; no semantic CLI command exists"
  evidence:   src/cairn/cli/ask_context.py:10 `@click.argument("question")` + `def ask(question, db, knowledge, as_json)`; cli/query.py commands are def/callers/search/callees/impact/deps — no `semantic`
  status:     DONE
  verify:     grep -B1 -A3 "^def " src/cairn/cli/query.py | grep "def "
  gap:        federated query path, --all-repos routing, per-repo attribution, unavailable-store reporting
```

## Supporting evidence
Dashboard already iterates workspaces (src/cairn/dashboard/workspaces.py) — a
consumer-side precedent for enumerating registry stores outside the active one.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
