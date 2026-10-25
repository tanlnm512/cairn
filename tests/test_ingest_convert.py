"""Binary-document conversion sidecar behind cairn[ingest]."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

from cairn.ingest_convert import _EXTRA_MISSING, convert_document, main


def _minimal_docx(path: Path, text: str) -> None:
    """Hand-build the smallest valid .docx (OPC zip) mammoth accepts."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType='
        '"application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type='
        '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="word/document.xml"/>'
        "</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)


def test_unsupported_suffix_skips(tmp_path):
    png = tmp_path / "img.png"
    png.write_bytes(b"\x89PNG")
    markdown, reason = convert_document(png)
    assert markdown is None
    assert reason.startswith("unsupported binary type")


def test_missing_extra_degrades_to_skip_reason(tmp_path, monkeypatch):
    """A None sys.modules entry makes the lazy import raise: the extra is absent."""
    monkeypatch.setitem(sys.modules, "pymupdf4llm", None)
    pdf = tmp_path / "spec.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real document")
    assert convert_document(pdf) == (None, _EXTRA_MISSING)


def test_cli_reports_skip_reason_on_stderr(tmp_path, monkeypatch, capsys):
    """`python -m cairn.ingest_convert <path>`: exit 1 with the reason; stdout stays empty."""
    monkeypatch.setitem(sys.modules, "mammoth", None)
    docx = tmp_path / "spec.docx"
    docx.write_bytes(b"not a zip at all")
    assert main([str(docx)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and _EXTRA_MISSING in err
    assert main([]) == 2


def test_docx_converts_to_markdown(tmp_path, capsys):
    pytest.importorskip("mammoth", reason="cairn[ingest] extra not installed")
    pytest.importorskip("markdownify", reason="cairn[ingest] extra not installed")
    docx = tmp_path / "spec.docx"
    _minimal_docx(docx, "Deploy the gateway")
    markdown, reason = convert_document(docx)
    assert reason is None and "gateway" in markdown.lower()
    assert main([str(docx)]) == 0
    assert "gateway" in capsys.readouterr().out.lower()

    garbage = tmp_path / "blank.docx"
    garbage.write_bytes(b"not a zip at all")
    markdown, reason = convert_document(garbage)
    assert markdown is None and reason.startswith("conversion failed")


def test_text_pdf_converts_to_markdown(tmp_path):
    pymupdf = pytest.importorskip("pymupdf", reason="cairn[ingest] extra not installed")
    pytest.importorskip("pymupdf4llm", reason="cairn[ingest] extra not installed")
    pdf = tmp_path / "spec.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Deploy the gateway", fontsize=12)
    doc.save(str(pdf))
    doc.close()
    markdown, reason = convert_document(pdf)
    assert reason is None and "gateway" in markdown.lower()
