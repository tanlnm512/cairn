"""Shared terminal display helpers for the cairn CLI.

Provides a single rich Console (auto-detects TTY), a themed color palette,
and a ``progress_bar()`` context manager for build/embed loops. TTY-aware:
when stdout isn't a terminal, colors are stripped and progress bars degrade
to plain ``N/M`` text.
"""
from __future__ import annotations

import sys
import time
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

# One console for the whole CLI process. Pass through whatever stdout is so
# piping to a file loses colors automatically.
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

# prompt_toolkit style strings for questionary/prompt_toolkit-based prompts
# (e.g. install-agents' checkbox). rich Style objects and prompt_toolkit style
# strings use incompatible color models; update both if the palette changes.
PROMPT_TOOLKIT_COLORS = {
    "info": "ansicyan",
    "success": "ansigreen",
    "warning": "ansiyellow",
    "dim": "ansibrightblack",
}


def is_tty() -> bool:
    """True iff stdout is an interactive terminal."""
    return sys.stdout.isatty()


# --- One-shot status helpers ----------------------------------------------
# Routes through the rich console so colors/theme apply.

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
    """Print a styled ``label: value`` line."""
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


# --- Non-TTY single-line progress -----------------------------------------
# Rich's Progress, when stdout isn't a TTY, prints a NEW line on every refresh
# instead of updating in place. This class mimics the slice of the Progress API
# that cairn callers use but renders a single line with \r.

class _SimpleTask:
    __slots__ = ("description", "total", "completed", "unit")

    def __init__(self, description: str, total: Optional[int], unit: str):
        self.description = description
        self.total = total
        self.completed = 0
        self.unit = unit


class _PlainTextProgress:
    """A minimal Progress-compatible renderer for non-TTY (piped/CI) output.

    Renders one line that updates in place via carriage return. Redraws at
    most once per 0.5s (time-throttled); description changes draw immediately
    but throttled to 0.2s. Exposes ``_cg_task_id``, ``update()``, ``advance()``,
    ``set_total()``, ``set_description()``, and ``tasks``.
    """

    def __init__(self, description: str, total: Optional[int], unit: str):
        self.tasks = [_SimpleTask(description, total, unit)]
        self._cg_task_id = 0
        self._last_desc = None
        self._t0 = time.time()
        # Start _last_draw at _t0 so the first non-forced draw is throttled.
        self._last_draw = self._t0

    def update(self, task_id: int, *, completed: Optional[int] = None,
               total: Optional[int] = None, description: Optional[str] = None,
               advance: Optional[int] = None) -> None:
        task = self.tasks[task_id]
        # Only treat description as a "force" draw if it actually changed.
        desc_changed = False
        if description is not None and description != task.description:
            task.description = description
            desc_changed = True
        if total is not None:
            task.total = total
        if completed is not None:
            task.completed = completed
        if advance is not None:
            task.completed += advance
        self._maybe_draw(force=desc_changed)

    def advance(self, amount: int = 1) -> None:
        self.update(self._cg_task_id, advance=amount)

    def set_total(self, new_total: int) -> None:
        self.update(self._cg_task_id, total=new_total)

    def set_description(self, desc: str) -> None:
        self.update(self._cg_task_id, description=desc)

    def _maybe_draw(self, force: bool = False) -> None:
        task = self.tasks[self._cg_task_id]

        # Time-based throttle is the PRIMARY gate: never draw more than once
        # per 0.5s. Description changes bypass it (so phase transitions are
        # visible immediately) but only if >0.2s has passed.
        now = time.time()
        elapsed_since_draw = now - self._last_draw
        desc_changed = task.description != self._last_desc

        should_draw = (
            force
            or elapsed_since_draw >= 0.5
            or (desc_changed and elapsed_since_draw >= 0.2)
        )
        if not should_draw:
            return

        self._last_desc = task.description
        self._last_draw = now

        # Build the line.
        elapsed = now - self._t0
        if task.total:
            pct = int(100 * task.completed / task.total)
            bar_width = 20
            filled = bar_width * task.completed // max(task.total, 1)
            bar = "█" * filled + "░" * (bar_width - filled)
            unit_str = f" {task.unit}" if task.unit else ""
            line = (
                f"\r{task.description}  [{bar}] "
                f"{task.completed:,}/{task.total:,}{unit_str}  {pct}%  {elapsed:.0f}s"
            )
        else:
            # Indeterminate: show count + elapsed.
            unit_str = f" {task.unit}" if task.unit else ""
            line = f"\r{task.description}  {task.completed:,}{unit_str}  {elapsed:.0f}s"

        sys.stdout.write(line)
        sys.stdout.flush()

    def start(self) -> None:
        self._maybe_draw(force=True)

    def stop(self) -> None:
        # Final draw at current state, then newline.
        self._maybe_draw(force=True)
        sys.stdout.write("\n")
        sys.stdout.flush()


class _RichProgressHandle:
    """Adapts a rich ``Progress`` + its one task to ``_PlainTextProgress``'s
    API so ``progress_bar()`` callers see the same contract on both backends.
    """

    def __init__(self, progress: Progress, task_id: int):
        self._progress = progress
        self._cg_task_id = task_id

    @property
    def tasks(self):
        return self._progress.tasks

    def update(self, task_id: int, **kwargs) -> None:
        self._progress.update(task_id, **kwargs)

    def advance(self, amount: int = 1) -> None:
        self._progress.update(self._cg_task_id, advance=amount)

    def set_total(self, new_total: int) -> None:
        self._progress.update(self._cg_task_id, total=new_total)

    def set_description(self, desc: str) -> None:
        self._progress.update(self._cg_task_id, description=desc)


# --- Progress bar context manager -----------------------------------------

@contextmanager
def progress_bar(
    description: str = "Working",
    total: Optional[int] = None,
    unit: str = "files",
    transient: bool = True,
) -> Iterator:
    """Yield a progress bar configured for the cairn CLI.

    ``total`` None means the caller must ``.update(task_id, total=N)`` later.
    TTY mode uses rich's animated Progress; non-TTY mode uses
    ``_PlainTextProgress`` (one line updated via ``\\r``).
    """
    if not is_tty():
        bar = _PlainTextProgress(description, total, unit)
        bar.start()
        try:
            yield bar
        finally:
            bar.stop()
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
        with progress:
            yield _RichProgressHandle(progress, task_id)


# --- Banner / panel for final summaries -----------------------------------

def summary_panel(title: str, kv_pairs: list[tuple[str, str]], subtitle: Optional[str] = None) -> None:
    """Print a final summary as a styled panel with ``kv_pairs`` lines inside."""
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
