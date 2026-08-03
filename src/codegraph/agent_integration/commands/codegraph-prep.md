# Pre-Work Context Loading

Before editing file: $ARGUMENTS

Prefer MCP tools (the `codegraph` server) for reads — they're faster and stay
within a session. Fall back to the `cg` CLI only if MCP is unavailable
(Golden Rule 10: reads via MCP, mutations via CLI).

1. `ask_compass(query="what should I know before editing $ARGUMENTS", file_path="$ARGUMENTS")` — load compass + memory + wiki for this file in one router call
2. `find_definition("<main-symbol>")` — confirm where the file's main symbol lives (derive main-symbol from the filename)
3. `get_callers("<main-symbol>")` — within-repo dependents (precise by default; auto-retries fuzzy if empty)
4. `cross_repo_deps("<repo>")` — cross-repo consumers (impact_analysis does NOT cross repos)
5. `impact_analysis("<main-symbol>")` — within-repo blast radius (precise by default). If the name is common/lifecycle-shaped like `get`/`create`/`onCreate`, run `scripts/impact_guard.py <main-symbol>` instead — see Golden Rule 6

CLI fallback (if MCP unavailable): `cg context $ARGUMENTS`, `cg def`, `cg callers`, `cg deps`, `cg impact`.

Summarize for the user:
- What module this file belongs to
- What the compass says about non-obvious patterns
- Any past memories about this file (search by symbol name)
- What will break if changes are made (within-repo + cross-repo consumers)

Present the summary as a brief context brief the user should read before coding.
