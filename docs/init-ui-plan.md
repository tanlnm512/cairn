# Vertical-Rail Flow UI for `cairn init` / `cairn build`

> **Status:** Design document, not yet implemented.
> **Goal:** Replace `cairn init`'s raw `click.echo` output with a clack-style vertical
> rail — a continuous `│` connecting every line, green `◆` step markers, animated
> sub-steps that settle in place, and a guaranteed `└` close. Adopt the same renderer
> in `cairn build`.

## Motivation

`cairn init` is the first command a user runs, and it currently has the worst output in
the CLI. It is the **only** major command that doesn't use `display.py` — it prints via
raw `click.echo` throughout (`core.py:35-91`), and it calls
`builder.build_graph(workspace, db_path, verbose=True)` with **no `progress=` callback**,
so the build phase dumps unstyled per-file `print()` noise from `builder._log`
(`builder.py:714`) straight to stdout. There is no progress feedback, no styling, and no
structure.

Target visual:

```
┌ Initializing cairn
│
◆ Initialized in /Users/tan.le/Projects/be/customer-android
│
◆ Store — /Users/tan.le/.cairn/a1b2c3
│    .kg         /Users/tan.le/.cairn/a1b2c3/.kg
│    .knowledge  /Users/tan.le/.cairn/a1b2c3/.knowledge
│
│  ◆ Scanning files — 3,295 found
│  ◆ Parsing code — done
│  ◆ Resolving refs — done
│  ◆ Persisting graph — done
│
◆ Indexed 3,295 files
│
● 80,445 nodes, 165,182 edges in 3.1s
│
└ Done
```

---

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| 4th sub-step | **`Persisting graph`**, off the existing `persist` event | The screenshot's "Linking dynamic dispatch" is `build_dataflow_index`, which today only `cairn build` runs. Adding it to `init` would be a behavior change (extra runtime, a build-complete DB) nobody asked for. |
| Scope | **`init` and `build`** | Both drive the same `build_graph` phase events; one renderer serves both. |
| Path lines | **Keep all four** (`Workspace`, `Store`, `.kg`, `.knowledge`) | `.kg`/`.knowledge` render as dim continuation lines under the Store step — no information lost vs. today. |
| Renderer home | **`display.py` only** | It is the sole `rich` boundary in the entire repo; every `rich` import lives there today. Preserve that invariant. |
| `build`'s summary panel | **Keep `summary_panel()` unchanged** | The rail replaces build's *progress bars*, not its detail table (repos/files/symbols/edges/resolution breakdown). |
| Rendering strategy | **Hybrid**: settled lines print permanently, the one active sub-step animates in a transient `Live` region | Long single-event phases (`Resolving refs`, `persist`) would otherwise look frozen for seconds; settled lines must scroll like normal output, not get erased. |

---

## Verified facts (from the codebase)

- **`rich>=13.0` is a core, non-optional dependency** (`pyproject.toml:47-50`), with a
  comment explaining it's used for "spinners, progress bars, ETA/rate, themed tables."
  No new dependency is needed. `rich.Live`, `Tree`, and `Status` are currently unused
  anywhere; `Console`, `Progress`, `Table`, `Theme`, `Panel`, `Text` are in use, all
  inside `display.py`.
- **`THEME`** (`display.py:31-39`) already defines exactly the styles this UI needs:
  `success` (bold green), `info` (cyan), `number` (bold blue), `dim`, `warning` (bold
  yellow), `error` (bold red), `label` (bold). **No new theme keys required.**
  `PROMPT_TOOLKIT_COLORS` (`display.py:46-51`) is a separate palette for
  questionary/prompt_toolkit prompts and is untouched by this change.
- **`build_graph(workspace, repo_filter=None, db_path=None, verbose=False, progress=None)`**
  (`builder.py:451`) takes a `progress(phase, **kw)` callback and emits, in order:
  `scan(files, skips)`, `parse_progress(done, total)`, `parse_done(parsed, errors)`,
  `insert_progress(done, total, symbols, edges)`, `resolve_start(repo)`,
  `resolve_done(repo, stats)`, `persist`. It returns a summary dict with
  `repos, files, symbols, edges, imports, skipped, resolution` (`resolution` has
  `exact`/`ambiguous`/`unresolved`).
- **Builder events must not be reshaped.** `tests/test_build_graph_decomposition.py`
  has golden assertions on the exact event sequence:
  `test_build_graph_golden_progress_event_sequence` (line 45) requires `scan` and
  `parse_progress` present and exactly one `parse_done` carrying a `parsed` kwarg;
  `test_build_graph_scan_event_shape` (line 160) requires exactly one `scan` event with
  non-negative `files`/`skips`. This design is pure consumer-side work — nothing in
  `builder.py` changes.
- **The scan itself runs before the first event fires.** There is no "scan started"
  event — `scan(files, skips)` reports the *completed* scan. So a "Scanning files" step
  can only be shown as active if the caller opens it before calling `build_graph` at all,
  and settles it on the `scan` event.
- **`build_graph` returns early on an empty workspace**, with a summary dict lacking the
  `resolution` key (and `skipped`). Any renderer built on the summary must guard the
  stats line for that case.
- **Rich permits exactly one active `Live` display per console.** At most one sub-step
  may be "active" at a time, and `display.progress_bar()` (used by `build`'s dataflow
  phase and by `embed`) must never be opened while a rail sub-step is live.
- **There is no encoding fallback anywhere in the repo today** — `success()`, `error()`,
  `info()`, and `_PlainTextProgress`'s block glyphs (`✓ ✗ ⠿ █ ░`) are written unguarded
  (`display.py:62-79`, `189`). The rail is new code and needs its own ASCII fallback for
  Windows/cp1252 terminals; retrofitting the rest of the module is out of scope.
- **Under `click.testing.CliRunner`, `sys.stdout.isatty()` is False**, so
  `display.is_tty()` returns False and any new non-TTY path must emit clean sequential
  lines — no ANSI cursor movement, no `\r` — for `CliRunner` output and CI/piped runs to
  stay readable.
- **Nothing today tests `init`'s output or `display.py` at all.** The existing
  printed-string assertions that constrain other commands —
  `tests/test_cli_smoke.py:34-39` (a `"graph"`/`"repos"` substring check on `cairn
  status`, sensitive to `display.kv`'s label text), `tests/test_uninstall_cmd.py`, and
  the dense set in `tests/test_trace_flow.py` — do not touch `init` or `build`.

---

## Design

### D1 — `rail()` + `Rail` in `display.py`

New code (~95 lines), inserted between `progress_bar()` and `summary_panel()`. New
imports: `re`, `from rich.live import Live`, `from rich.text import Text` (the local
`Text`/`Panel` imports already inside `summary_panel()` are left as-is — surgical diff).

```python
@contextmanager
def rail(title: str, animate: bool = True) -> Iterator["Rail"]:
    """Render a vertical-rail flow; `└` is written on every exit path.

    Do not open a ``progress_bar()`` while a sub-step is active: rich permits
    only one live display at a time.
    """

class Rail:
    # depth 0 — own marker, always preceded by a rail-only spacer
    def step(self, text, value=None) -> None       # ◆ success
    def detail(self, label, value) -> None          # dim continuation line, no marker
    def warn(self, text) -> None                    # ! warning
    def stat(self, text) -> None                    # ● info

    # depth 1 sub-steps — indented 2, still carry the outer rail
    def start(self, label) -> None                  # opens the animated sub-step
    def tick(self, value) -> None                    # updates the active value
    def finish(self, value="done", ok=True) -> None  # settles as ◆ label — value
```

**Why imperative, not a nested context manager.** `build_graph`'s feedback arrives
through a `progress(phase, **kw)` callback — a state machine, not nested scopes. A
per-sub-step context manager (`with rail.task(...)`) would force `ExitStack` juggling
inside the callback; the imperative form lets each phase branch be exactly one call.
`start()` and every depth-0 method **implicitly settle whatever sub-step is currently
open** (as `finish()` with its default `"done"`), so callers never have to track state.
`tick()`/`finish()` with nothing open are no-ops — a reordered or dropped event can never
raise from the renderer.

**Rendering strategy.**
- **TTY** (`animate=True`): settled lines go through `console.print` (permanent, scroll
  naturally). The single in-progress sub-step lives in
  `Live(transient=True, refresh_per_second=10)`. On `finish()`, the `Live` stops
  (erasing its region) and the settled line is printed — the "completes in place" effect.
  Invariant: **never `console.print` while the `Live` is active** — every method stops
  the `Live` first, and the group spacer is printed *before* `Live.start()`.
- **Non-TTY, or `animate=False`**: no `Live`, no ANSI. `start()` only records the label;
  `tick()` is a no-op; `finish()` prints the one settled line. `animate=False` exists so
  `cairn build -v` (which turns on `builder._log`'s raw `print()`) can't have its own
  output corrupt a live region — see D3.

**Spinner.** Needed: `Resolving refs` and `Persisting graph` emit a single event each and
would otherwise look frozen for seconds. Rich's `SpinnerColumn`/`Spinner` render
`frame + text` with no way to prepend the rail's `│  ` prefix, and `console.status` gives
no prefix control either. Instead, a small renderable
(`__rich_console__`) picks a braille frame from `time.monotonic()`; `Live`'s own refresh
thread animates it, so no extra throttling code is needed. `tick()` must not pass
`refresh=True` — it just mutates `.value` and lets the refresh thread coalesce however
many events arrive (parse+insert alone is on the order of thousands on a large repo).

**Glyphs**, selected once per `Rail` instance (in `__init__`, not at import time, so
`CliRunner` and piped output re-resolve correctly) via an encode-probe against
`console.encoding`:

| role | unicode | ascii |
|---|---|---|
| open | `┌` | `+` |
| rail / spacer | `│` (dim) | `\|` |
| close | `└` | `+` |
| step / sub-step done | `◆` (success) | `*` |
| active | `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` (info) | `\|/-\` |
| stat | `●` (info) | `o` |
| failed | `✗` (error) | `x` |
| warn | `!` (warning) | `!` |
| separator | `—` (dim) | `-` |

**Indentation.** Depth 0 → `f"{marker} {text}"`. Depth 1 → `f"{bar}  {marker} {text}"`
(rail, two spaces, marker, space) — matches `│  ◆ Scanning files`. `detail()` →
`f"{bar}    {label:12}{value}"`, entirely dim. Only depths 0 and 1 exist; there is no
`depth=` parameter, since nothing in either command needs a third level.

**Spacers are automatic**, so callers never encode layout: a rail-only `│` line is
emitted before every depth-0 line, and before the first depth-1 line of a run
(`depth == 0 or depth != self._prev_depth`). `detail()` emits no spacer of its own and
leaves `_prev_depth` untouched, so the *next* step still gets one. `_open()` bypasses the
rule (it's the first line). Applied to the target visual, this reproduces every blank
rail line exactly, including the one before `└ Done`.

**Value highlighting.** Values are never passed as rich markup strings — a workspace
path containing `[` would corrupt markup, and `init` prints user paths. Instead every
text/value segment is built as `rich.text.Text` and highlighted structurally:

```python
_NUM_RE = re.compile(r"(?<![\w./-])\d[\d,]*(?:\.\d+)?[a-z]{0,2}\b")

def _value(s: str) -> Text:
    t = Text(s)
    t.highlight_regex(_NUM_RE, "number")   # existing THEME style: bold blue
    return t
```

One rule covers all three shapes in the target visual: `3,295 found`, `3.1s`, and the
composite `80,445 nodes, 165,182 edges in 3.1s` (numbers bold blue, everything else
default). The `(?<![\w./-])` lookbehind keeps digits inside paths and identifiers
(`proj2`, `/2024-repo`, `v0.6.0`) unhighlighted. Thousands separators remain the caller's
job (`f"{n:,}"`), matching `build`'s existing style.

**Guaranteed close.** `rail()` is a `@contextmanager`, same shape as the existing
`progress_bar()`:

```python
r = Rail(); r._open(title)
try:
    yield r
except BaseException:          # includes KeyboardInterrupt
    r._fail()                  # settles the active step as ✗ … — failed, then "└ Failed"
    raise
r._close("Done")
```

`_close` is guarded by a `self._closed` flag (idempotent) and stops any live region
before writing, so no path can leave the terminal with a running `Live` or an
unterminated rail.

### D2 — `init` (`core.py:19-91`)

Wrap the body in `with display.rail("Initializing cairn") as r:`, replacing every
`click.echo`. Phase → sub-step mapping:

| sub-step | driven by |
|---|---|
| `Scanning files — 3,295 found` | `r.start("Scanning files")` **before** calling `build_graph` (the scan completes before the first event — see Verified facts), settled on `scan` |
| `Parsing code` | started when `scan` fires; `parse_progress` ticks `50*done/total`%, `insert_progress` ticks `50 + 50*done/total`% |
| `Resolving refs` | started on the first `resolve_start` (flag-guarded — it fires once per repo); ticks the repo name |
| `Persisting graph` | started on `persist` |

Merging parse+insert into one sub-step follows the precedent already in `build`
(`total_files * 2` at `core.py:199`), but shows a **percentage** rather than a raw
`done/total`, which hides the doubling from the user. `parse_done` needs no branch.

Depth-0 lines: `r.step("Initialized in", str(ws))`, `r.step("Store", str(store.home))`
plus `r.detail(".kg", str(store.db))` / `r.detail(".knowledge", str(store.knowledge))`,
conditionally `r.step(f"Migrated from {legacy}", ", ".join(migrated))`, then after the
build `r.step(f"Indexed {summary['files']:,} files")` and
`r.stat(f"{symbols:,} nodes, {edges:,} edges in {elapsed:.1f}s")`. `Graph already
present` and `No docs/ directory found` become `r.warn(...)`. The trailing hint
(`cairn config` / `cairn serve`) prints via `display.dim()` **after** the rail closes,
so `└ Done` stays the last rail line.

Two required fixes:
- **Drop `verbose=True`** from `init`'s `build_graph` call. One word: it removes the
  per-file `print()` noise the rail replaces, and removes the only stdout writer that
  could corrupt the `Live` region. `cairn build -v` remains the way to get per-file
  detail.
- Guard the stats line: `if not summary.get("files"): r.warn("No source files found")`
  else the two closing lines — `build_graph` returns early on an empty workspace without
  `resolution`/`skipped`.

### D3 — `build` (`core.py:183-321`)

Replace the three `display.progress_bar()` blocks with one rail carrying:
`Scanning files`, `Parsing code`, `Resolving refs`, `Persisting graph`, then
`Dataflow index` (ticking `done/pub_total` via the existing
`build_dataflow_index(conn, progress=lambda done: ...)` callback) and
`Transitive closure`.

**Keep `summary_panel()` exactly as-is**, printed after the rail closes. The rail
replaces build's *progress rendering*; the panel still carries the detail table
(`repos/files/symbols/edges/imports/skipped/dataflow/transitive`) plus the resolution
subtitle and the `--staging` atomic-swap note. Build's final output becomes a rail
followed by its panel — nothing is dropped.

Three behaviors from the current implementation that must survive the rewrite:
- **`--staging` temp-DB cleanup** (`core.py:236-245`): keep the `try/except` around
  `build_graph` inside the rail body. `rail()` re-raises on any exception, so the
  cleanup still runs and the rail closes as `└ Failed`.
- **`-v/--verbose`** deliberately turns on `builder._log`'s raw `print()`, which would
  corrupt a live region. Pass the flag through as `display.rail(title, animate=not
  verbose)`, so verbose builds render the rail as plain sequential lines with no live
  region. This is the one extra parameter the design admits, and it exists specifically
  to protect the "never print while `Live` is active" invariant.
- The dataflow connection's `try/except/finally` (`core.py:254-280`) is unchanged — a
  leaked connection holds SQLite's writer lock and blocks subsequent `build`/`embed`.

`display.progress_bar()` itself is **kept, not removed** — it remains the right widget
for `embed`'s single determinate loop, which is untouched by this design.

---

## Change list

| File | Change |
|---|---|
| `src/cairn/cli/display.py` | + `rail()`, `Rail`, `_ActiveLine`, `_GLYPHS`, `_unicode_ok()`, `_value()`, `_NUM_RE`. ~95 new lines, nothing removed. |
| `src/cairn/cli/core.py` | rewrite `init` (lines 19-91) and `build` (lines 183-321) to drive the rail instead of raw `click.echo` / `progress_bar()`. |
| `tests/test_display_rail.py` | NEW — golden non-TTY output block, exception path, ASCII fallback, no-op robustness, number highlighting. |
| `tests/test_cli_init_rail.py` | NEW — `CliRunner` end-to-end smoke test on `init --no-build`. |
| `CHANGELOG.md` | feature note. |

`src/cairn/graph/builder.py` is read-only reference for this design — **no event
changes**.

---

## Risks & mitigations

- **Concurrent `Live`** → `LiveError`. Mitigated by the "at most one active sub-step"
  invariant plus the `rail()` docstring warning; `init` opens no progress bars, and
  `build`'s bars are all replaced by the rail (no overlap).
- **Stray stdout inside a step** → dropping `verbose=True` in `init` and the
  `animate=not verbose` guard in `build` close both known sources. Residual: stderr
  warnings from `graph/config.py` / `graph/cross_repo.py` on a malformed `cairn.json`
  interleave visually but can't corrupt rich's cursor math (different stream) —
  acceptable.
- **Line wrapping** on narrow terminals: a long workspace path wraps and the
  continuation has no rail glyph (cosmetic). Leaving wrap on is the right default —
  truncating a path the user wants to copy is worse.
- **Windows/cp1252**: covered by `_unicode_ok()`. This makes the rail the *only*
  encoding-safe output in the CLI; `success()`/`error()`/`_PlainTextProgress` remain
  unguarded elsewhere in `display.py`. Extending `_GLYPHS` to them is a clean follow-up,
  out of scope here.
- **Event volume**: parse+insert alone emit on the order of thousands of `tick()` calls
  on a large workspace; each is a string format plus an attribute set, and `Live`'s
  refresh thread (10/sec) coalesces the actual redraws. Verify with a real multi-thousand-file
  workspace that CPU during parse is unchanged from today.
- **Implicit auto-settling**: a caller that emits `step()` mid-phase silently marks the
  currently open sub-step "done". This is intentional — it's what makes the callback
  branches in D2/D3 one-liners — and is documented on the methods themselves.

## Verification (for the implementation follow-up)

1. **Non-TTY golden block**: `monkeypatch.setattr(display, "is_tty", lambda: False)` and
   swap `display.console` for `Console(file=StringIO(), width=100, theme=display.THEME)`;
   drive intro → step → detail → start/tick/finish ×2 → step → stat → exit; assert the
   exact multi-line string, pinning glyphs, the two-space sub-step indent, the `—`
   separator, and every spacer position. This is the regression net for the whole visual.
2. **Exception path**: raising inside `with rail(...)` re-raises *and* the captured
   output ends with the `✗ … — failed` sub-step line followed by the closing glyph line.
3. **ASCII fallback**: point the console's file at a
   `TextIOWrapper(BytesIO(), encoding="cp1252")`, render, assert no
   `UnicodeEncodeError` and that `|`/`*` appear instead of `│`/`◆`.
4. **Robustness**: `tick()`/`finish()` with no active step are no-ops; double-close
   writes exactly one `└`.
5. **Highlighting**: `Console(record=True, force_terminal=True)` +
   `export_text(styles=True)`; assert `3,295` and `3.1s` carry the `number` style while
   `/Users/a/proj2` does not.
6. **CLI end-to-end**: `CliRunner().invoke(main, ["init", "--workspace", tmp, "--no-build"])`
   with `cairn.paths.CAIRN_HOME`/`REGISTRY_FILE` monkeypatched to a tmpdir (they are
   module-level constants resolved at import, so `setenv` alone is insufficient) —
   assert `exit_code == 0`, `"Initialized in"` present, closing glyph present, and no
   ANSI escapes in the output.
7. **Regression guard**: re-run `tests/test_build_graph_decomposition.py`,
   `tests/test_cli_smoke.py`, and `pytest -m core` — no builder events change, so all
   must pass untouched.
8. **Real terminal**: run `cairn init`, `cairn build`, `cairn build -v`, and
   `cairn build 2>&1 | cat` against a real multi-thousand-file workspace to confirm the
   animation, in-place settling, verbose fallback, and piped output all look right.
