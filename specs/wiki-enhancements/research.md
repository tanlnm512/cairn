# Research: wiki-enhancements

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-31
<!-- External grounding for tech decisions: every claim below carries a source
     URL/DOI — no unsourced "it is known that". The tech agent consumes this
     file when choosing options in tech-spec.md. -->

## Questions
<!-- One subsection per research question (the orchestrator supplies 3-6,
     derived from the spec's open technical choices). Record 2-3 findings per
     question; if none is credible, keep the fallback line verbatim. -->

### <research question 1>
- **source**: <URL or DOI> · **claim**: <one sentence> · **relevance**: <which
  question / FR it informs> · **confidence**: high | med | low
- no credible source found — decide from first principles <!-- fallback only -->

### <research question 2>
<!-- ... one subsection per remaining question ... -->

## Options summary
<!-- ≤15 lines. For each open choice: the credible candidates and the
     one-line trade-off between them. NO recommendation — that is the tech
     agent's job with your data. -->

### <open choice>
- <candidate A> — <one-line trade-off>
- <candidate B> — <one-line trade-off>


**Researcher gate: skipped — no open questions at Stage 0.**
All ten FRs are incremental changes over machinery surveyed exhaustively
earlier today (specs/archive/2026-08-31-wiki-generation/survey.md remains the
ground-truth baseline: task queue, critic, OKF, catalog planner, renderer,
CLI/MCP/dashboard conventions). No library, algorithm, or protocol choice is
open: GFM tables are a fixed spec; commit-sha resolution reuses the graph
builder's existing git integration; export is file writing; enrich rides the
existing promotion branch (kind prefix `wiki-page` already routes there).
