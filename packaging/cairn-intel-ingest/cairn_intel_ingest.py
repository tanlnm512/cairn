"""Optional high-fidelity PDF-to-markdown converter invoked by the engine through sidecar resolution."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

#: Suffixes this module converts; docx converts natively inside the engine.
CONVERT_SUFFIXES = frozenset({".pdf"})

_EXTRA_MISSING = "cairn-intel-ingest dependencies not installed"
_EMPTY_EXTRACTION = "empty extraction"


def convert_document(path: Path) -> tuple[str | None, str | None]:
    """Convert one supported binary document or return a skip reason."""
    suffix = path.suffix.lower()
    if suffix not in CONVERT_SUFFIXES:
        return None, f"unsupported binary type: {suffix}"
    try:
        return _convert_pdf(path)
    except ImportError:
        return None, _EXTRA_MISSING
    except Exception as e:  # a corrupt file must skip, not crash the run
        return None, f"conversion failed: {e}"


def _convert_pdf(path: Path) -> tuple[str | None, str | None]:
    import pymupdf4llm

    markdown = pymupdf4llm.to_markdown(str(path))
    return _checked(markdown)


def _checked(markdown: str | None) -> tuple[str | None, str | None]:
    """Accept only extractions with real text content."""
    if markdown is None or not markdown.strip():
        return None, _EMPTY_EXTRACTION
    return markdown, None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`python -m cairn_intel_ingest <path>`: markdown on stdout, or the skip reason on stderr with exit 1."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m cairn_intel_ingest <document.pdf>", file=sys.stderr)
        return 2
    markdown, reason = convert_document(Path(args[0]))
    if markdown is None:
        print(reason, file=sys.stderr)
        return 1
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
