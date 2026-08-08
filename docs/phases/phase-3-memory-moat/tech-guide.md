# Phase 3 — Memory Moat (Technical Guide)

> **Roadmap milestone:** Month 3 — Turn proof into the memory moat.
> **Read alongside:** [`spec.md`](spec.md), [`plan.md`](plan.md).
> **Rule:** every file/symbol reference is verified against the tree. Re-run
> the verification commands before implementing; if they disagree, the guide
> is wrong.

## Important: verification already runs on recall

Phase 3 was scoped assuming `recall_memory` does *not* ground memories in
symbols. **That is wrong.** Graph-grounded verification already runs on both
the digest view *and* recall:

- `src/cairn/memory/scoring.py:111` — `_graph_verification(concept, conn)`
  checks backtick-quoted file/symbol refs in a memory body against the graph
  and returns a verified fraction (0.0–1.0). It uses the shared extractors
  `extract_file_refs` / `extract_symbol_refs` from `src/cairn/refs.py` (the
  same ones the critic imports).
- `src/cairn/mcp_server/tools_memory.py:22` — `memory_digest` calls it
  (`:46`) and surfaces `refs-verified=<fraction>` (`:49`).
- `src/cairn/mcp_server/tools_memory.py:59` — `recall_memory` **also** calls
  it (`:124`, importing at `:83`) and surfaces `refs-verified=<fraction>`
  (`:128`). Its docstring (`:62-67`) documents this.

So the **detection + surfacing of the fraction** already exists on recall.
What does *not* exist:

- A discrete **`stale` flag** derived from the fraction — today the number is
  shown but no boolean verdict is computed (the gap 3.1 closes).
- `cairn update` does **not** warn when an edited file is referenced by a
  memory (the gap 3.2 closes).

Phase 3's work is therefore **add a verdict on top of the existing fraction**,
not build verification from scratch. Re-verify before starting:

```bash
grep -n "_graph_verification" src/cairn/memory/scoring.py
grep -n "refs-verified\|_graph_verification" src/cairn/mcp_server/tools_memory.py
# recall_memory DOES verify — expect _graph_verification at the import + the call site
```

---

## 3.1 — Graph-grounded memory recall verdict

**Existing mechanism:** `_graph_verification(concept, conn)` in
`src/cairn/memory/scoring.py:111`. It extracts backtick-quoted refs from the
body via the shared extractors in `src/cairn/refs.py` and checks each against
the graph (file existence via the `files` table, symbol existence via the
`symbols`/FTS index). `recall_memory` already calls it per match
(`tools_memory.py:124`) and surfaces `refs-verified=<fraction>` (`:128`).

**Actual work (only the verdict remains):**
- In `recall_memory` (`tools_memory.py:113-133`), after computing
  `refs_verified` at line 124, compare it to the chosen threshold and append
  a `stale` tag to the result line at 128. The fraction and per-memory
  conn-reuse are already in place.
- Choose the threshold deliberately (e.g. `< 1.0` for "some ref stale",
  `< 0.5` for "mostly stale") and document it. Do not silently pick.
- No schema migration needed — verification is computed live from the body
  text, exactly as `recall_memory`/`memory_digest` do today. The roadmap's
  "symbol-link column" idea is a Phase 4 optimization (cache the result);
  Phase 3 reuses the live computation.

**Pitfalls:**
- `_graph_verification` inspects **backtick-quoted** refs only (same as the
  critic). A memory that mentions `ApiFactory` without backticks is not
  verified. That is consistent with the doc layer — keep it; do not invent a
  new extraction rule for memory alone.
- Performance: the per-recall verification is already incurred today; adding
  the flag is O(1) per memory. No new cost.

## 3.2 — Memory-triggered build hints

**Existing path:** `src/cairn/cli/update.py` + `src/cairn/graph/incremental.py`.
`update.py` already runs memory decay after reindex (the `decay(bundle)` call
at line 78) and surfaces warnings (lines 57–65) — so the warning channel exists.

**Actual work:**
- After `cairn update` detects changed files (the incremental reindex output),
  for each changed file, scan the tribal-memory corpus for memories whose
  backtick refs point at symbols *in that file*. Reuse the shared extractors
  `extract_file_refs` / `extract_symbol_refs` in `src/cairn/refs.py` (also
  imported by `compass/critic.py` and `memory/scoring.py` as
  `_extract_file_refs`) to find candidate memories, then check whether the
  cited symbol's `file_id` matches a changed file.
- If matches exist, emit a warning through the existing `display.warning`
  channel: "N memor(y/ies) reference symbols in <file> — verify before
  relying on impact results: <memory paths>."
- Keep it a warning; `cairn update` must not fail on memory state.

**Pitfalls:**
- **False positives are the main risk.** Only flag memories with explicit
  backtick-quoted refs resolving to a changed file's symbols — never loose
  textual mentions. A file named in prose is not an anchor.
- The scan over all tribal memories per changed file could be expensive on
  large corpora. For Phase 3, accept the cost and note it; an index from
  `symbol_id → memory_path` is the Phase 4 optimization (this is where the
  roadmap's "symbol-link column" would pay off).
- Symbol identity across rename: a renamed symbol is a new `symbol_id`, so
  the memory's cited (old) symbol won't match the new one — the memory
  effectively becomes "stale" via 3.1's recall check. The build hint should
  say "symbols changed," not "memory broken."

## Reusing Phase 2.2's verdict shape

Both 3.1 and 3.2 surface a verification result. Reuse Phase 2.2's
`critic`-verdict shape (`{passed, errors, warnings}` or a `refs-verified`
fraction) so the product has one consistent "trustworthiness" vocabulary
across docs and memory, not two.

---

## Verification commands (run before starting each item)

```bash
# Confirm the graph-verification mechanism exists
grep -rn "_graph_verification" src/cairn/memory/scoring.py src/cairn/mcp_server/tools_memory.py

# Confirm recall_memory ALREADY verifies (expect 2: import at :83, call at :124)
sed -n '59,138p' src/cairn/mcp_server/tools_memory.py | grep -c "_graph_verification"  # expect 2

# Confirm the update path's warning channel
grep -n "display.warning\|memory" src/cairn/cli/update.py

# Confirm the critic extractors that 3.2 reuses
grep -n "_extract_file_refs\|_extract_symbol_refs" src/cairn/compass/critic.py
```
