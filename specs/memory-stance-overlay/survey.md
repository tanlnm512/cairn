# Survey: memory-stance-overlay

**Created**: 2026-10-05 | **Baseline**: 0.21.2 @ 69d4009
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "Memories are tiered OKF markdown files, not rows — atomic per-file writes are the natural write model"
  evidence:   src/cairn/memory/store.py:1 `"""Memory storage: tiered OKF files (raw/drafts/tribal/archived)."""` — TIER_DIRS :15-20 maps each tier to its memory/tribal-style directory (raw, drafts, tribal, archived); built on OKFBundle/OKFConcept (:13-14)
  status:     DONE
  verify:     sed -n '1,20p' src/cairn/memory/store.py
  gap:        stance is new frontmatter metadata on those files

item S2: "No stance field exists on memory records"
  evidence:   src/cairn/mcp_server/tools_memory.py:166 `def record_memory(` with signature `(type, title, body, resource, confidence) -> str` — no stance parameter
  status:     TODO
  verify:     grep -n "stance" src/cairn/memory/ src/cairn/mcp_server/tools_memory.py   (no match)
  gap:        optional stance param on the MCP tool, the CLI command, and the OKF frontmatter model

item S3: "CLI `cairn memory record` lacks stance"
  evidence:   src/cairn/cli/memory.py:39 `@memory.command("record")` :51 `def memory_record(mtype, title, body, resource, confidence, recurrence_key, db, knowledge):`
  status:     TODO
  verify:     cairn memory record --help
  gap:        add --stance flag (preferred|tentative|contested)

item S4: "refs-verified fraction is live, per-query machinery — reusable for reflect and staleness"
  evidence:   src/cairn/cli/memory.py:15 "``refs-verified`` is the live backtick-ref fraction ('?' when ``conn`` is" :32 `return f"  [{tier}{score}, refs-verified={refs}] {c.title}{cid}"` :343 `help="Graph DB path (enables refs-verified fractions).")`
  status:     DONE
  verify:     cairn memory search reflect --help
  gap:        none — reflect reuses this computation; no re-derivation needed

item S5: "Supersession chains exist at capture time — the disagreement signal for contradiction detection"
  evidence:   src/cairn/mcp_server/tools_memory.py:194 `superseded = result.get("superseded")` (capture_memory result); supersession surfaced in the record message
  status:     DONE
  verify:     grep -n "superseded" src/cairn/memory/promotion.py | head -5
  gap:        reflect consumes existing supersession links + refs-verification; co-citation alone must NOT imply contradiction (agent bus is a different mechanism)

item S6: "check_overlap is the AGENT-symbol bus, not memory-vs-memory comparison — a trap for this spec"
  evidence:   src/cairn/memory/store.py:292 `def check_overlap(` docstring "Return other agents' recent ``agent_symbols`` activity on ``symbols``" (:256 `def share_memory(` inserts agent-symbol rows)
  status:     DONE
  verify:     sed -n '292,300p' src/cairn/memory/store.py
  gap:        none as long as reflect's contradiction rule is built from refs + supersession, NOT from check_overlap

item S7: "explore renders a Tribal memory section — the surfacing point for stance"
  evidence:   src/cairn/mcp_server/tools_graph.py:602 `# --- Tribal memory section ---` :603 `out.append(f"=== Tribal memory ({len(tribal)}) ===")`
  status:     TODO
  verify:     grep -n "Tribal memory" src/cairn/mcp_server/tools_graph.py
  gap:        append stance inline per memory line when set, with peer reference for contested

item S8: "CLI search/list lines already carry tier+score+refs — stance slot exists in the line format"
  evidence:   src/cairn/cli/memory.py:12 `"""One memory-listing line: tier/score prefix then refs-verified fraction, then title [id].` `_memory_line` :12, used by search (:153 `def memory_search(`)
  status:     TODO
  verify:     cairn memory search stance --db nonexistent
  gap:        extend _memory_line with stance when set

item S9: "Lifecycle tiers/scoring are score-mapped and separate from any stance"
  evidence:   src/cairn/memory/store.py:32 `def tier_for_score(score: float) -> str:` (below 0.3 raw, below 0.5 drafts, else tribal)
  status:     DONE
  verify:     grep -n "def tier_for_score" src/cairn/memory/store.py
  gap:        none — stance is orthogonal by construction (FR-001); reflect must not touch tier/score

item S10: "No reflect command exists"
  evidence:   src/cairn/cli/memory.py subcommands: record :39, evolve :87, search :141, timeline :202, capture :251 — no reflect
  status:     TODO
  verify:     cairn memory --help
  gap:        new `cairn memory reflect` subcommand + deterministic stance rules
```

## Supporting evidence

- Memory CLI group: src/cairn/cli/memory.py:35 `def memory():`.
- Memory tools module: src/cairn/mcp_server/tools_memory.py:16 `def recall_memory(` — recall is the MCP read path where stance must also render (FR-004).
- Capture pipeline: tools_memory.py:180-186 `capture_memory(conn, bundle, type_=..., title=..., body=..., resource=..., confidence=...)` returns path/signals/tier — the single choke point stance metadata rides through.
- OKF concept model: src/cairn/okf/concept.py (OKFConcept) and src/cairn/okf/bundle.py (OKFBundle) own frontmatter serialization; stance joins the same round-trip.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
