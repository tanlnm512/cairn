# Tech Spec: memory-stance-overlay

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05
**Every file/symbol citation below must come verbatim from [survey.md](survey.md)
or a grep run in this session — never from memory.**

## Architecture

Memories are tiered OKF markdown files (survey S1), so stance is file
frontmatter, not a database row. The feature has three moving parts, all
existing infrastructure plus one new pure module:

1. **Capture plumbing** (exists, extended): `capture_memory` in
   `src/cairn/memory/promotion.py` is the single choke point every recorder
   rides through (survey supporting evidence); it gains the record-time
   stance prior and seeds the refs baseline.
2. **Reflect** (new): a deterministic, LLM-free pass over the store that
   reads each memory's backtick refs against the live graph (reusing
   `_graph_verification` in `src/cairn/memory/scoring.py`, survey S4) and the
   frontmatter supersession chains (`memory_superseded_by`, survey S5), then
   writes only stance keys back per file.
3. **Surfacing** (exists, extended): the three render points that already
   list memories — recall (survey supporting evidence), explore's tribal
   section (survey S7), and the CLI line format (survey S8) — append the
   stance inline when set.

```mermaid
flowchart LR
    rec["record / capture / evolve"] -->|prior stance + seeded baseline| fm["memory file frontmatter"]
    sup["supersession chains in frontmatter"] -->|read| refl["cairn memory reflect"]
    graph["live graph via _graph_verification"] -->|verified fraction| refl
    refl -->|per-file atomic write, stance keys only| fm
    fm -->|read extensions| recall["recall_memory"]
    fm -->|read extensions| explore["explore tribal section"]
    fm -->|read extensions| cli["CLI search / list / digest"]
```

## Solution

### Chosen approach

Stance state is three optional frontmatter extension keys on the memory
concept (D-003): `memory_stance` (preferred | tentative | contested; absent =
unset, FR-001), `memory_stance_peer` (bundle-relative concept id of the
contradicting peer, set only when contested, FR-003), and
`memory_refs_baseline` (the recorded refs-verified fraction FR-005 measures
drops against). Orthogonal to lifecycle state by construction: reflect never
writes `memory_tier`, `memory_score`, `memory_is_latest`, `title`, or `body`
(survey S9).

**Stance authority is BOTH** (D-001, the FR-002 ruling):
`--stance` on `cairn memory record` (survey S3) and a `stance` parameter on
the MCP `record_memory` tool (survey S2) record a *prior* through
`capture_memory`; `cairn memory reflect` recomputes an *evidence verdict*
that overrides the prior whenever the evidence yields one. When evidence
yields no verdict, the prior survives untouched.

**Reflect's verdict rules are pinned and deterministic** (D-002, D-004;
survey S10 requires deterministic stance rules). Per memory, in precedence
order contested > tentative > preferred > prior:

- *Contested*: the memory has a `memory_superseded_by` edge to a peer N
  (the existing supersession chain, survey S5) **and** the two memories share
  at least one symbol ref that verifies in the live graph. The memory is
  contested with peer N's id (FR-003). A superseded memory with no shared
  verified ref is not contested — it falls through to the staleness rule.
- *Tentative*: the memory has at least one backtick ref and its live
  refs-verified fraction is below `memory_refs_baseline` — code changed under
  it (FR-005).
- *Preferred*: the memory has at least one backtick ref and every ref
  verifies (fraction 1.0) — verified-citation agreement (FR-002).
- *No verdict*: memories with zero backtick refs get no verification verdict
  (mirroring recall's "n/a (0 refs)" distinction in
  `src/cairn/mcp_server/tools_memory.py`), so a prose-only memory keeps its
  prior stance or stays unset.
- *Baseline*: seeded at capture from the live fraction (when refs exist) and
  refreshed by reflect as a high-water mark: baseline = max(baseline, current)
  (D-006). A memory whose fraction recovers to baseline is preferred again.

Idempotency and determinism (FR-006): the verdict is a pure function of
(frontmatter, body refs, graph state, supersession edges); memories are
processed in sorted concept-id order; reflect writes a file only when a
stance key would actually change, and it never writes timestamps. On an
unchanged store the second run is byte-for-byte a no-op.

**Rendering is inline-append only** (FR-004): `_memory_line` in
`src/cairn/cli/memory.py` (survey S8) appends `, stance=<value>` after the
refs segment and a peer suffix for contested; the recall render loop in
`src/cairn/mcp_server/tools_memory.py` appends the same bracket segment plus
a peer line under contested entries (same shape as its existing STALE hint
line); explore's tribal section in `src/cairn/mcp_server/tools_graph.py`
(survey S7) appends the stance and peer to the memory's existing title line.
No new output lines are added except the contested peer hint, so pinned
output assertions hold (see Impact analysis).

FR coverage: FR-001 capture plumbing + frontmatter keys; FR-002 reflect
command + verdict rules; FR-003 stance-keys-only writes + peer identity;
FR-004 inline rendering at all three surfacing points; FR-005 baseline
staleness rule; FR-006 pure-function verdicts, sorted order, no-op re-runs.

### Alternatives rejected

| Alternative | Why rejected |
|-------------|--------------|
| LLM-judged contradiction over co-cited memories | Survey S10 pins deterministic stance rules and FR-006 requires idempotent determinism; an LLM verdict is neither |
| Using `check_overlap` / the `agent_symbols` table as the contradiction source | Survey S6: check_overlap returns other agents' recent agent_symbols activity — an agent-symbol bus with no memory-vs-memory semantics |
| Contested from co-citation alone | Survey S5 gap: co-citation alone must NOT imply contradiction; benign complementary memories would be flagged (spec risk 1) |
| SQL projection row for stance (the `memory_validity` pattern) | No render path reads stance from SQL — all three read concept extensions; a second write path would break the per-file atomic model (NFR-004) |
| Auto-reflect riding `cairn update` | Spec Scope marks it out: explicit command only in this spec |
| New MCP tool for reflect | Survey S10: a CLI subcommand; keeping MCP untouched preserves the pinned tool count and the memory verb surface (D-005) |

## Impact analysis

Blast radius of the touched symbols (cairn graph tools, this session):

- `capture_memory`: precise impact analysis at depth 3 = 75 impacted
  symbols/tests (24 direct callers at depth 1, spanning the CLI record/capture
  verbs, the MCP `record_memory` tool, and the lifecycle/validity/redaction
  test suites); no cross-repo dependents. The change is an additive optional
  `stance` kwarg — kwargs-less and keyword call sites stay green.
- `_memory_line`: precise callers resolve to 0 (private helper); the three
  real call sites read this session in `src/cairn/cli/memory.py` are
  `memory_search` (:191), `memory_list` (:362), and `memory_digest` (:407).
  All three gain stance rendering through the one helper — no per-caller work.
- `recall_memory` and the explore tribal section are leaf render paths
  (MCP tools); callers are the MCP registry, untouched.

Test-tree sweep for the changed APIs, classified (the flip here is additive,
so there are no flag pins; the render additions are where pins live):

| Test (file · name) | Class | Pin | Effect |
|--------------------|-------|-----|--------|
| `tests/test_agent_surface.py` · `test_tools_md_default_args_match_live_signatures` | exact-traffic | Parses every documented MCP signature default in `src/cairn/agent_integration/skill/references/tools.md` against the live `inspect.signature` | Breaks unless the `record_memory` docs add `stance` with its live default in the same task |
| `tests/test_agent_surface.py` · `test_cli_commands_in_skill_docs_exist` | exact-traffic | Every documented `cairn <group> <sub>` must resolve in the scraped Click registry | Docs citing `cairn memory reflect` land only with the verb (same task) |
| `tests/test_memory_stale_flag.py` · `test_recall_flags_stale_when_cited_symbol_deleted`, `test_recall_no_stale_flag_for_real_refs`, `test_recall_partial_stale_when_one_of_two_refs_gone`, `test_recall_no_stale_flag_for_memory_without_refs`, `test_recall_does_not_crash_when_verification_raises` | exact-traffic | Assert exact `refs-verified=<value>` substrings and the `[STALE]` tag of the recall line | Green iff the stance segment appends after the refs value; the pinned substrings must not move or reformat |
| `tests/test_explore_memory.py` · section asserts at :102, :118, :142, :173, :209 | exact-count | Pins `=== Tribal memory (N) ===` headers and per-memory body lines | Green iff stance is appended to the existing title line — no new lines, no header change |
| `tests/test_memory_lifecycle.py` · `test_same_title_same_type_supersedes`, `test_version_chain_inherited` | behavior | Pins the `memory_superseded_by` / `memory_supersedes` frontmatter edge | Green: reflect reads these keys, never writes them |
| `tests/test_memory_validity.py` · `test_capture_stamps_extensions_and_projection_row` | behavior | Pins capture-time extension stamping | Green: new keys are additive and optional |
| `tests/test_memory_mcp_trim.py` · `test_six_memory_cli_verbs_remain_reachable` | behavior | Pins the mutating memory verbs through `cairn memory` | Green: reflect is a new read-modify verb; no existing verb changes |
| `tests/test_memory_stale_flag.py` · `test_recall_no_stale_flag_for_memory_without_refs` | behavior | Pins the zero-refs "n/a" distinction recall draws | Green: reflect applies the same zero-refs rule (no verification verdict) |

## Quality, threats, and rollback

| Requirement | Design consequence / threat mitigation | Rollback or verification |
|-------------|----------------------------------------|--------------------------|
| NFR-003 (reflect completes within 30s wall on the primary store) | Reflect is a single pass: O(memories + distinct refs); ref-existence lookups are memoized per run so each backtick ref hits the graph once; no embeddings, no semantic scan | Time `cairn memory reflect` on the primary workspace store; budget check in the Phase 2 checkpoint |
| NFR-004 (interrupted reflect leaves memory files uncorrupted) | Writes go through `bundle.write_concept` in `src/cairn/okf/bundle.py` (atomic os.replace per file) under `bundle.lock()` — the store's existing write model (survey S1); an interrupted run leaves each file wholly pre- or post-write | Kill reflect mid-run in a test fixture; every memory file must still parse (`OKFConcept.from_file`); recovery is re-running reflect (verdicts are recomputable) |
| NFR-001 (security) | Not applicable per spec: local file metadata only, no new surface — reflect reads and writes the same local bundle the memory CLI already owns | None needed |
| NFR-002 (privacy) | Not applicable per spec: no new data leaves the store; reflect output is counts and ids already on disk | None needed |
| NFR-005 (observability) | Not applicable per spec: the command's own summary output (changed/unchanged counts) suffices | Summary covered by the reflect verb task |
| NFR-006 (accessibility) | Not applicable per spec: no UI surface is touched | None needed |

Threat model (persistence is the touched category): asset = memory files
under the bundle's memory/ tiers; threat = a reflect bug mass-rewriting or
truncating memories; mitigation = reflect writes only the three stance keys,
one atomic os.replace per file, serialized with concurrent agents by
`bundle.lock()`; residual risk = a semantic bug setting wrong stances, which
is recoverable because stances are a pure recompute (fix the rule, re-run).
Rollback strategy: the stance keys are additive; deleting them from the
frontmatter restores the exact pre-feature file state.

## Code guide

### Capture plumbing (stance prior + baseline seed)
- Touches: `create_memory` in `src/cairn/memory/store.py` (survey S2, S9:
  lifecycle extensions live here; stance joins as optional keys) and
  `capture_memory` in `src/cairn/memory/promotion.py` (survey supporting
  evidence: the single choke point), plus the `record_memory` MCP signature in
  `src/cairn/mcp_server/tools_memory.py` (survey S2).
- Approach: optional validated `stance` kwarg threaded to `create_memory`;
  after scoring, seed `memory_refs_baseline` from the live fraction when the
  body has refs; update `src/cairn/agent_integration/skill/references/tools.md`
  in the same change (pin in Impact analysis).
- Verify before implementing: `grep -n "stance" src/cairn/memory/ src/cairn/mcp_server/tools_memory.py` (survey S2 verify: no match today).
- Pitfalls: never write `memory_tier`/`memory_score` from stance logic
  (survey S9); a zero-ref memory gets no baseline (D-004).

### Reflect engine (new pure module + CLI verb)
- Touches: a new stance-rules module under `src/cairn/memory/` next to
  `promotion.py`; the `reflect` verb in `src/cairn/cli/memory.py` (survey
  S10: no reflect subcommand exists today; survey S3 shows the subcommand
  registration pattern).
- Approach: pure verdict functions (D-002, D-004) over concepts +
  verification fractions + supersession edges; the verb loads the bundle,
  iterates `list_memories` in sorted concept-id order, writes changed files
  via `bundle.write_concept` under `bundle.lock()`, prints a changed/unchanged
  summary. Reuse `_graph_verification` (survey S4) and the `_norm_cid`
  relative-id convention from `src/cairn/memory/promotion.py` for peer ids.
- Verify before implementing: `cairn memory --help` (survey S10 verify: no reflect).
- Pitfalls: co-citation is not contradiction (survey S5/S6 — D-002);
  non-latest memories are hidden from default recall but still get stances
  (timeline and `--include-superseded` readers surface them).

### Surfacing (inline stance render)
- Touches: `_memory_line` in `src/cairn/cli/memory.py` (survey S8), the
  recall render loop in `src/cairn/mcp_server/tools_memory.py` (survey
  supporting evidence), the tribal section in `src/cairn/mcp_server/tools_graph.py`
  (survey S7).
- Approach: append `, stance=<value>` inside the existing bracket after the
  refs segment; contested entries get the peer id inline (CLI line) or as a
  peer hint line under the entry (recall), mirroring the existing STALE hint
  shape; explore appends to the existing title line only.
- Verify before implementing: `grep -n "Tribal memory" src/cairn/mcp_server/tools_graph.py` (survey S7 verify); `cairn memory search stance --db nonexistent` (survey S8 verify).
- Pitfalls: the exact-traffic pins in Impact analysis — the pinned
  `refs-verified=` substrings and tribal header lines must not move; docs
  (`docs/cli-reference.md` Memory section) updated in the same PR.

## References
- [research.md](research.md): no open questions at Stage 0 — no external
  candidates to weigh.
- Survey S4 (live refs machinery), S5/S6 (supersession chains vs the
  agent-symbol bus), S9 (tier/score orthogonality): the three constraints the
  design is built on.

## Decisions
<!-- ADR-lite. Append-only: decisions made during implementation land here too. -->
### D-001: Stance authority is BOTH — record-time prior, reflect verdict wins
- **Context**: FR-002's stance-authority contract (dual source, evidence wins).
- **Decision**: Both. `--stance` on `cairn memory record` and a `stance`
  parameter on the MCP `record_memory` tool write a prior through
  `capture_memory`; `cairn memory reflect` recomputes from evidence and its
  verdict (contested/tentative/preferred) overrides the prior; with no
  evidence verdict the prior stands.
- **Consequences**: the stance frontmatter key is writable by both paths;
  reflect must never treat an unset prior as a verdict source; a user-set
  `contested` on a fully-verified, non-superseded memory survives reflect
  (no evidence verdict fires to override it).

### D-002: Contradiction = supersession edge + shared verified ref, never co-citation
- **Context**: The spec's top risk is flagging benign co-cited memories as
  contradicting peers; the detection source had to be pinned deterministically.
- **Decision**: Reflect declares memory M contested with peer N iff M carries
  a `memory_superseded_by` edge to N (the existing supersession chain) AND M
  and N share at least one symbol ref that verifies in the live graph.
  `check_overlap` is untouched. Fixture evidence: the edge already exists as
  frontmatter — `tests/test_memory_lifecycle.py` `test_same_title_same_type_supersedes`
  asserts `old.extensions["memory_superseded_by"] == r2["path"]` and the
  `memory_supersedes` chain; and `check_overlap` in `src/cairn/memory/store.py`
  (:292) returns `(agent_id, symbol, kind, ts)` rows from `agent_symbols` —
  the agent-symbol bus (survey S6), with no memory-pair semantics, so
  co-citation there can never found a contradiction.
- **Consequences**: two live memories citing the same symbol without a
  supersession edge are complementary and never contest each other; the peer
  reference (FR-003) always points at the memory's recorded successor.

### D-003: Stance state lives in OKF frontmatter, written per-file atomically
- **Context**: FR-003 requires reflect to modify only stance metadata; FR-004
  requires NFR-004-grade write safety; memories are tiered OKF files
  (survey S1).
- **Decision**: Three optional extension keys on the memory concept —
  `memory_stance`, `memory_stance_peer`, `memory_refs_baseline` — serialized
  by the existing frontmatter round-trip in `src/cairn/okf/concept.py` and
  written only via `bundle.write_concept` (atomic os.replace) under
  `bundle.lock()` in `src/cairn/okf/bundle.py`. Reflect writes nothing else —
  never `memory_tier`/`memory_score` (survey S9), never body/title, never the
  supersession keys.
- **Consequences**: no SQL schema or projection row; any bundle copy/move
  carries stance for free; per-file atomicity is inherited, not rebuilt.

### D-004: Pinned verdict precedence — contested > tentative > preferred > prior
- **Context**: FR-002/FR-005/FR-006 need one deterministic rule set; a memory
  can match several rules at once (a superseded memory whose refs also rotted).
- **Decision**: Contested (D-002) wins; else tentative when the memory has
  refs and the live fraction is below baseline (FR-005); else preferred when
  it has refs and every ref verifies; else no verdict and the record-time
  prior (D-001) stands or the memory stays unset. Zero-ref memories get no
  verification verdict, matching the recall path's "n/a (0 refs)" distinction.
  Processing order is sorted concept id; reflect writes a file only when a
  stance key changes (FR-006).
- **Consequences**: a superseded memory with rotted refs is contested, not
  tentative; a superseding memory's own stance follows the verification rules;
  identical stores always produce identical stances, peers, and order.

### D-005: Reflect is a CLI verb only — no new MCP tool
- **Context**: FR-002 names `cairn memory reflect`; adding an MCP tool would
  grow the pinned tool-count surface.
- **Decision**: Implement reflect as a `cairn memory` subcommand only
  (survey S10). The MCP tool surface, `_EXPECTED_TOOL_COUNT`, and the memory
  verb set pinned by `tests/test_memory_mcp_trim.py`
  `test_six_memory_cli_verbs_remain_reachable` are untouched.
- **Consequences**: agents invoke reflect via the CLI fallback path;
  surfacing stays read-only in MCP (recall/explore render, never recompute).

### D-006: Baseline is a per-file high-water mark seeded at capture
- **Context**: FR-005 measures a *drop* against a *recorded* baseline; the
  live fraction alone cannot distinguish "rotted" from "always low", and a
  DB-only baseline would break the file-carried model (D-003).
- **Decision**: `capture_memory` seeds `memory_refs_baseline` with the live
  fraction at capture time (only when the body has refs); reflect updates it
  to max(baseline, current). Kept redundancy beside `memory_score`'s
  verification signal is deliberate: the score is a weighted blend that is
  never a drop detector, and it is refreshed on recapture, not on code change.
- **Consequences**: a memory whose fraction recovers to baseline is preferred
  again without reseeding; a first reflect run on a legacy store seeds
  missing baselines (seed-only, no downgrade on that first pass), which keeps
  FR-006 idempotency intact.
