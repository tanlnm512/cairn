"""Binary-document conversion behind the cairn[ingest] extra (D-002).

PDF via pymupdf4llm; docx via mammoth (HTML) -> markdownify. All heavy
imports are strictly lazy: the base install imports this module cleanly
and a missing extra degrades to a skip with a reason, never a crash.
"""
from __future__ import annotations

from pathlib import Path

#: Suffixes this module converts; everything else is left to markdown paths.
CONVERT_SUFFIXES = frozenset({".pdf", ".docx"})

_EXTRA_MISSING = "cairn[ingest] not installed"
_EMPTY_EXTRACTION = "empty extraction"

#: Tag carried by every accepted conversion (FR-003).
CONVERTED_TAG = "converted"


def convert_document(path: Path) -> tuple[str | None, str | None]:
    """Convert one pdf/docx file to markdown.

    Returns ``(markdown, None)`` on success or ``(None, skip_reason)``
    when the extra is missing or the extraction is empty/garbage.
    """
    suffix = path.suffix.lower()
    if suffix not in CONVERT_SUFFIXES:
        return None, f"unsupported binary type: {suffix}"
    try:
        if suffix == ".pdf":
            return _convert_pdf(path)
        return _convert_docx(path)
    except ImportError:
        return None, _EXTRA_MISSING
    except Exception as e:  # a corrupt file must skip, not crash the run
        return None, f"conversion failed: {e}"


def _convert_pdf(path: Path) -> tuple[str | None, str | None]:
    import pymupdf4llm

    markdown = pymupdf4llm.to_markdown(str(path))
    return _checked(markdown)


def _convert_docx(path: Path) -> tuple[str | None, str | None]:
    import markdownify
    import mammoth

    with open(path, "rb") as fh:
        result = mammoth.convert_to_html(fh)
    html = result.value
    if not html.strip():
        return None, _EMPTY_EXTRACTION
    markdown = markdownify.markdownify(html, heading_style="ATX")
    return _checked(markdown)


def _checked(markdown: str | None) -> tuple[str | None, str | None]:
    """Accept only extractions with real text content."""
    if markdown is None or not markdown.strip():
        return None, _EMPTY_EXTRACTION
    return markdown, None
