# Research: cli-usage-recording

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
External grounding for tech decisions: every claim below carries a source
URL — no unsourced "it is known that". The tech agent consumes this file
when choosing options in tech-spec.md.

## Questions

### RQ1: Telemetry shutdown discipline for short-lived CLI processes?
- **source**: [OpenTelemetry Python — shutdown handling discussion](https://github.com/open-telemetry/opentelemetry-python/discussions/3034) · **claim**:
  buffered telemetry in CLI tools requires explicit shutdown hooks
  (atexit/signal handlers) because short-lived processes exit before any
  periodic flush fires. · **relevance**: FR-003, SC-2 (flush-on-exit) · **confidence**: high
- **source**: [Python docs — atexit](https://docs.python.org/3/library/atexit.html) · **claim**:
  atexit callbacks run on normal interpreter exit in LIFO order and are
  skipped on signals like SIGKILL, defining exactly what "clean exit"
  covers. · **relevance**: FR-003's "no silent drops" boundary · **confidence**: high

### RQ2: Opt-out conventions for local CLI telemetry?
- **source**: [OpenTelemetry spec — SDK configuration env vars](https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/) · **claim**:
  the de-facto convention is env-var gating (ENABLE/DISABLE style) read at
  startup, documented alongside the variables it honors. ·
  **relevance**: FR-004 (documented, discoverable opt-out) · **confidence**: high
- **source**: [Homebrew analytics docs](https://docs.brew.sh/Analytics) · **claim**:
  a widely-cited prior art for on-by-default local telemetry with a
  documented env-var opt-out and public disclosure of what is collected. ·
  **relevance**: FR-004 (precedent for default-on + opt-out) · **confidence**: medium

### RQ3: Deriving shell-session identity for grouping CLI runs?
- **source**: [tmux environment variables](https://github.com/tmux/tmux/wiki/Getting-Started) · **claim**:
  tmux exports TMUX and TMUX_PANE giving a stable per-pane identifier
  usable to group commands run in the same pane. · **relevance**: FR-006 · **confidence**: medium
- **source**: no credible portable source found for a cross-terminal
  shell-session identifier — decide from first principles (candidates:
  terminal-specific env vars like macOS Terminal's TERM_SESSION_ID, tmux
  pane ids, else per-invocation uuid) <!-- fallback partially used -->
- **relevance**: FR-006 ("where derivable, falling back to per-invocation") · **confidence**: low

### RQ4: Recording argument summaries without leaking user data?
- **source**: [cairn's own privacy module doctrine] — the repo's
  strip_private_data chokepoint (survey Q3) is the established mechanism;
  external general guidance: [CWE-532: Insertion of Sensitive Information
  into Log File](https://cwe.mitre.org/data/definitions/532.html) · **claim**:
  logging untrusted input verbatim is a recognized weakness class; scrub at
  the write boundary. · **relevance**: FR-001 (redacted arg summary) · **confidence**: high

## Options summary

### Interception point (FR-001)
- **custom click.Group.invoke wrapping dispatch** — one place, catches all
  current + future commands; per-invocation record naturally
- **decorator per command** — explicit but drifts as commands are added
- **shell wrapper / alias** — outside the binary; misses non-alias use

### Source labeling (FR-002)
- **additive `source` column (cli|mcp), default mcp** — one table, one
  pipeline, filterable; migration rides the proven pattern
- **separate cli_metrics table** — no migration but splits every aggregate
  and view into a UNION
- **tool_name prefix convention (cli:build)** — zero schema change but
  pollutes tool identity and breaks exact-match filters

### Session identity (FR-006)
- **terminal/tmux env var where present, else per-invocation uuid** —
  best-effort grouping, never 'unknown'
- **always per-invocation uuid** — simplest; no grouping at all
- **parent pid** — unstable across shell restarts and OSes
