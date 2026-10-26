"""Optional high-fidelity PDF converter package (cairn-intel-ingest)."""
from __future__ import annotations

import sys

import pytest

from cairn_intel_ingest import _EXTRA_MISSING, convert_document, main


def test_unsupported_suffix_skips(tmp_path):
    """docx converts natively in the engine; the package carries PDF only."""
    docx = tmp_path / "spec.docx"
    docx.write_bytes(b"PK")
    markdown, reason = convert_document(docx)
    assert markdown is None
    assert reason.startswith("unsupported binary type")


def test_missing_extra_degrades_to_skip_reason(tmp_path, monkeypatch):
    """A None sys.modules entry makes the lazy import raise: the dependency is absent."""
    monkeypatch.setitem(sys.modules, "pymupdf4llm", None)
    pdf = tmp_path / "spec.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real document")
    assert convert_document(pdf) == (None, _EXTRA_MISSING)


def test_cli_reports_skip_reason_on_stderr(tmp_path, monkeypatch, capsys):
    """`python -m cairn_intel_ingest <path>`: exit 1 with the reason; stdout stays empty."""
    monkeypatch.setitem(sys.modules, "pymupdf4llm", None)
    pdf = tmp_path / "spec.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real document")
    assert main([str(pdf)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and _EXTRA_MISSING in err
    assert main([]) == 2


def test_text_pdf_converts_to_markdown(tmp_path):
    pymupdf = pytest.importorskip("pymupdf", reason="cairn-intel-ingest dependencies not installed")
    pytest.importorskip("pymupdf4llm", reason="cairn-intel-ingest dependencies not installed")
    pdf = tmp_path / "spec.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Deploy the gateway", fontsize=12)
    doc.save(str(pdf))
    doc.close()
    markdown, reason = convert_document(pdf)
    assert reason is None and "gateway" in markdown.lower()

    garbage = tmp_path / "blank.pdf"
    garbage.write_bytes(b"not a pdf at all")
    markdown, reason = convert_document(garbage)
    assert markdown is None and reason.startswith("conversion failed")
