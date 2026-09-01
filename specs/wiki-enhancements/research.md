# Research: wiki-enhancements

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-31

## Not applicable — no open questions at Stage 0

**Researcher gate: skipped.** Stage 1 ran as a solo surveyor spawn; no
researcher agent was spawned and no external grounding was gathered. This
file is the deliberate record of that ruling, not an unfinished stage.

All ten FRs are incremental changes over machinery surveyed exhaustively
earlier the same day (the wiki-generation spec set's survey.md remains the
ground-truth baseline: task queue, critic, OKF, catalog planner, renderer,
CLI/MCP/dashboard conventions; that spec set was removed from the tree in
5f4995d and is readable at
`git show 088d026:specs/archive/2026-08-31-wiki-generation/survey.md`).
No library, algorithm, or protocol choice is open:

- GFM tables are a fixed, published spec — no candidate selection to make.
- Commit-sha resolution reuses the graph builder's existing git integration
  (`src/cairn/utils/git.py`), already vendored in-repo.
- Export is plain file writing over the existing OKF concept round-trip.
- Enrich rides the existing promotion branch (the kind prefix `wiki-page`
  already routes there).

Every rejected alternative in [tech-spec.md](tech-spec.md) therefore traces
to a survey constraint rather than to an external source. If a genuinely open
question emerges during implementation, the researcher is spawned then and
this file is replaced with its findings.
