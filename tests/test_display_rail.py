"""Tests for the vertical-rail flow renderer (``display.rail`` / ``Rail``).

Covers the regression net called out in docs/init-ui-plan.md §Verification:
golden non-TTY output, the exception path, the ASCII fallback, no-op
robustness, and number highlighting. All cases force a non-animated path
(animate=False or a non-TTY console) so output is deterministic.
"""
from __future__ import annotations

from io import BytesIO, StringIO
from textwrap import dedent

import pytest
from rich.console import Console

from cairn.cli import display


def _capture(**console_kwargs) -> str:
    """Replace ``display.console`` with a capturing Console and return output."""
    buf = StringIO()
    kwargs = {"file": buf, "width": 100, "force_terminal": False, "theme": display.THEME}
    kwargs.update(console_kwargs)
    display.console = Console(**kwargs)
    display.is_tty = lambda: False
    return buf


@pytest.fixture(autouse=True)
def _restore_console():
    orig_console = display.console
    orig_istty = display.is_tty
    orig_unicode_ok = display._unicode_ok
    yield
    display.console = orig_console
    display.is_tty = orig_istty
    display._unicode_ok = orig_unicode_ok


# ---------------------------------------------------------------------------
# 1. Non-TTY golden output
# ---------------------------------------------------------------------------

def test_golden_non_tty_output():
    """The full depth-0 + depth-1 + detail/stat flow renders verbatim."""
    buf = _capture()
    with display.rail("Initializing cairn", animate=False) as r:
        r.step("Initialized in", "/Users/x/proj")
        r.step("Store", "/home/.cairn/abc")
        r.detail(".kg", "/home/.cairn/abc/.kg")
        r.detail(".knowledge", "/home/.cairn/abc/.knowledge")
        r.start("Scanning files")
        r.tick("3,295 found")
        r.finish("3,295 found")
        r.start("Parsing code")
        r.finish("done")
        r.step("Indexed 3,295 files")
        r.stat("80,445 nodes, 165,182 edges in 3.1s")

    expected = dedent("""\
        ┌ Initializing cairn
        │
        ◆ Initialized in — /Users/x/proj
        │
        ◆ Store — /home/.cairn/abc
        │    .kg         /home/.cairn/abc/.kg
        │    .knowledge  /home/.cairn/abc/.knowledge
        │
        │  ◆ Scanning files — 3,295 found
        │  ◆ Parsing code — done
        │
        ◆ Indexed 3,295 files
        │
        ● 80,445 nodes, 165,182 edges in 3.1s
        │
        └ Done
        """)
    assert buf.getvalue() == expected


def test_step_without_value_renders_label_only():
    buf = _capture()
    with display.rail("T", animate=False) as r:
        r.step("Just a label")
    out = buf.getvalue()
    assert "◆ Just a label\n" in out
    # No trailing separator when value is None.
    assert "—" not in out


def test_warn_uses_warning_glyph():
    buf = _capture()
    with display.rail("T", animate=False) as r:
        r.warn("Graph already present")
    out = buf.getvalue()
    assert "! Graph already present" in out


# ---------------------------------------------------------------------------
# 2. Exception path
# ---------------------------------------------------------------------------

def test_exception_path_closes_as_failed_and_reraises():
    """Raising inside the rail re-raises and the rail closes as └ Failed."""
    buf = _capture()
    with pytest.raises(RuntimeError, match="boom"):
        with display.rail("Initializing", animate=False) as r:
            r.start("Parsing code")
            r.tick("50%")
            raise RuntimeError("boom")
    out = buf.getvalue()
    assert "✗ Parsing code — failed" in out
    assert out.rstrip().endswith("└ Failed")


# ---------------------------------------------------------------------------
# 3. ASCII fallback
# ---------------------------------------------------------------------------

def test_ascii_fallback_when_encoding_unsupported():
    """A cp1252 console must not raise and must use ASCII glyphs."""
    import io
    raw = BytesIO()
    wrapper = io.TextIOWrapper(raw, encoding="cp1252")
    display.console = Console(file=wrapper, width=100, force_terminal=False,
                              theme=display.THEME)
    display.is_tty = lambda: False
    display._unicode_ok = lambda: False  # force ASCII glyph set

    with display.rail("Init", animate=False) as r:
        r.step("Done", "3 files")
        r.stat("100 nodes in 1.2s")
    wrapper.flush()
    out = raw.getvalue().decode("cp1252")
    # cp1252 can encode ASCII but not the box glyphs; no UnicodeEncodeError,
    # and the ASCII replacements appear.
    assert "+" in out and "|" in out and "*" in out
    assert "●" not in out and "◆" not in out


# ---------------------------------------------------------------------------
# 4. No-op robustness
# ---------------------------------------------------------------------------

def test_tick_and_finish_with_no_active_step_are_noops():
    buf = _capture()
    with display.rail("T", animate=False) as r:
        r.tick("anything")          # no active sub-step
        r.finish("done")            # no active sub-step
        r.finish()                  # double-finish safety
        r.step("After")
    out = buf.getvalue()
    assert "◆ After" in out
    # No stray sub-step lines leaked from the no-op calls.
    assert "│  ◆ anything" not in out


def test_start_implicitly_settles_prior_sub_step():
    """Opening a new sub-step settles the open one as 'done'."""
    buf = _capture()
    with display.rail("T", animate=False) as r:
        r.start("First")
        r.start("Second")   # 'First' must be settled automatically
        r.finish("ok")
    out = buf.getvalue()
    assert "◆ First — done" in out
    assert "◆ Second — ok" in out


def test_step_mid_substep_settles_it():
    """A depth-0 call mid-phase marks the open sub-step done."""
    buf = _capture()
    with display.rail("T", animate=False) as r:
        r.start("Parsing code")
        r.step("Indexed 10 files")   # implicitly finishes 'Parsing code'
    out = buf.getvalue()
    assert "◆ Parsing code — done" in out
    assert "◆ Indexed 10 files" in out


def test_double_close_writes_one_close_glyph():
    buf = _capture()
    r = display.Rail(display._unicode_ok(), animate=False)
    r._open("T")
    r._close("Done")
    r._close("Done")  # idempotent
    out = buf.getvalue()
    assert out.count("└ Done") == 1


# ---------------------------------------------------------------------------
# 5. Number highlighting
# ---------------------------------------------------------------------------

def test_numbers_highlighted_and_paths_are_not():
    """Counts and durations get the 'number' style; embedded path digits don't."""
    console = Console(record=True, width=100, force_terminal=True, theme=display.THEME)
    display.console = console
    display.is_tty = lambda: False
    display._unicode_ok = lambda: True
    with display.rail("T", animate=False) as r:
        r.stat("80,445 nodes, 165,182 edges in 3.1s")
        r.step("Scanned", "/Users/a/proj2/repo2024")
    # export_text() clears the record buffer on each call, so capture both
    # forms up front from the styled one (it embeds the plain text too).
    styled = console.export_text(styles=True)
    # \x1b[1;34m is THEME's "number" = bold blue.
    assert "\x1b[1;34m80,445\x1b[0m" in styled
    assert "\x1b[1;34m3.1s\x1b[0m" in styled
    # A path like proj2 / repo2024 should NOT carry a bold-blue run: assert
    # the literals appear unstyled (no escape wrap around them).
    assert "proj2" in styled
    assert "repo2024" in styled
    assert "\x1b[1;34mproj2\x1b[0m" not in styled
