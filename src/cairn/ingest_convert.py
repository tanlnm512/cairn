"""Binary-document conversion sidecar behind the cairn[ingest] extra (PDF and docx to markdown)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

#: Suffixes this module converts; everything else is left to markdown paths.
CONVERT_SUFFIXES = frozenset({".pdf", ".docx"})

_EXTRA_MISSING = "cairn[ingest] not installed"
_EMPTY_EXTRACTION = "empty extraction"


def convert_document(path: Path) -> tuple[str | None, str | None]:
    """Convert one supported binary document or return a skip reason."""
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`python -m cairn.ingest_convert <path>`: markdown on stdout, or the skip reason on stderr with exit 1."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m cairn.ingest_convert <document.pdf|document.docx>", file=sys.stderr)
        return 2
    markdown, reason = convert_document(Path(args[0]))
    if markdown is None:
        print(reason, file=sys.stderr)
        return 1
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
