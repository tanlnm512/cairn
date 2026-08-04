"""Shared terminal display helpers for the cairn CLI.

Centralizes all rich-based output so every command renders consistently:
a single Console (auto-detects TTY), a themed color palette, and a
``progress_bar()`` context manager for build/embed loops.

Design notes:
  - **TTY-aware.** When stdout isn't a terminal (piped to a file, CI logs),
    rich is configured with ``force_terminal=False`` so it strips colors and
    progress bars degrade to plain ``N/M`` text. This keeps ``cairn build |
    tee build.log`` and CI captures readable.
  - **No globals beyond the console.** Each command that needs a progress
    bar opens one via ``with progress_bar() as bar:`` -- nested progress
    would clash if shared.
  - **Theme.** One place (THEME) defines the color for each semantic role
    (success/warning/error/info/dim), so restyling is one edit.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator, Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.theme import Theme

# One console for the whole CLI process. force_terminal defaults to auto-detect;
# we pass through whatever stdout is, so piping to a file loses colors
# automatically (rich checks isatty under the hood when force_terminal is None).
THEME = Theme({
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "info": "cyan",
    "dim": "dim",
    "label": "bold",
    "number": "bold blue",
})

console = Console(theme=THEME)


def is_tty() -> bool:
    """True iff stdout is an interactive terminal.

    Used to decide whether to show spinners/animated bars at all. In CI or
    piped contexts we fall back to plain text to avoid escape-sequence soup
    in log files.
    """
    return sys.stdout.isatty()


# --- One-shot status helpers ----------------------------------------------
# Each takes an optional ``file`` (for click.echo compatibility) but routes
# through the rich console so colors/theme apply. Use these instead of
# click.echo for any status-style output.

def success(msg: str) -> None:
    console.print(f"[success]✓[/success] {msg}")


def warning(msg: str) -> None:
    console.print(f"[warning]![/warning] {msg}")


def error(msg: str) -> None:
    console.print(f"[error]✗[/error] {msg}")


def info(msg: str) -> None:
    console.print(f"[info]⠿[/info] {msg}")


def dim(msg: str) -> None:
    console.print(msg, style="dim")


# --- Labeled key/value pairs (for stats/status) ---------------------------

def kv(label: str, value, indent: int = 0) -> None:
    """Print a ``label: value`` line with the label styled.

    Used by cairn status / cairn stats to render the per-layer rollups in a
    consistent aligned style.
    """
    pad = " " * indent
    console.print(f"{pad}[label]{label:14}[/label] [number]{value}[/number]")


# --- Tables (for stats / status rollups) -----------------------------------

def print_table(title: Optional[str], columns: list[str], rows: list[list]) -> None:
    """Render a titled table. Auto-sized columns; right-aligns numeric-looking cells."""
    table = Table(title=title, title_style="bold", show_header=True, header_style="bold cyan")
    for col in columns:
        # Right-align columns whose header looks numeric (count, n, etc.)
        justify = "right" if col.lower() in {"count", "n", "calls", "symbols", "edges", "files", "repos"} else "left"
        table.add_column(col, justify=justify)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)


# --- Progress bar context manager -----------------------------------------
# Used by cairn build (parse/insert/resolve phases) and cairn embed (batch loop).

@contextmanager
def progress_bar(
    description: str = "Working",
    total: Optional[int] = None,
    unit: str = "files",
    transient: bool = True,
) -> Iterator[Progress]:
    """Yield a rich Progress configured for the cairn CLI.

    ``description``: the label shown to the left of the bar (e.g. "Parsing").
    ``total``: if known up-front, the bar shows determinate progress + ETA.
               If None, the caller must ``.update(task_id, total=N)`` later.
    ``unit``: shown after the count ("1,234 files", "64 symbols").
    ``transient``: when True (default), the live bar is erased on success
                   and replaced by a one-line summary -- cleaner scrollback.
                   Pass False to keep the completed bar in the log.

    In a non-TTY (CI/pipe), rich automatically renders one line per refresh
    instead of animating, so the progress is still visible in logs.
    """
    if not is_tty():
        # Non-TTY: still use Progress so callers have a consistent API, but
        # disable the spinner (would render as a frozen glyph) and the bar's
        # in-place refresh. TimeRemainingColumn is useless without a TTY.
        columns = [
            TextColumn("[bold]{task.description}[/bold]"),
            MofNCompleteColumn(),
            TextColumn(unit),
            TimeElapsedColumn(),
        ]
    else:
        columns = [
            SpinnerColumn(style="info"),
            TextColumn("[bold]{task.description}[/bold]"),
            BarColumn(complete_style="success", finished_style="success"),
            MofNCompleteColumn(),
            TextColumn(unit),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ]
    progress = Progress(*columns, console=console, transient=transient and is_tty())
    task_id = progress.add_task(description, total=total)
    try:
        # Attach the task_id to the progress object so callers can do
        # `bar.advance(bar._task_id)` or `bar.update(bar._task_id, ...)`
        # without needing to thread the id through their own state.
        progress._cg_task_id = task_id  # type: ignore[attr-defined]
        progress.advance = lambda amount: progress.update(task_id, advance=amount)  # type: ignore[assignment]
        progress.set_total = lambda new_total: progress.update(task_id, total=new_total)  # type: ignore[assignment]
        progress.set_description = lambda desc: progress.update(task_id, description=desc)  # type: ignore[assignment]
        with progress:
            yield progress
    finally:
        pass


# --- Banner / panel for final summaries -----------------------------------

def summary_panel(title: str, kv_pairs: list[tuple[str, str]], subtitle: Optional[str] = None) -> None:
    """Print a final summary as a styled panel.

    ``title``: heading (e.g. "Built graph in 12.3s").
    ``kv_pairs``: list of (label, value) shown as ``label value`` lines inside.
    """
    from rich.panel import Panel
    from rich.text import Text

    body = Text()
    for i, (label, value) in enumerate(kv_pairs):
        if i:
            body.append("\n")
        body.append(f"{label:14} ", style="label")
        body.append(value, style="number")
    if subtitle:
        body.append("\n\n", style="dim")
        body.append(subtitle, style="dim")

    console.print(Panel(body, title=title, title_align="left", border_style="success"))
