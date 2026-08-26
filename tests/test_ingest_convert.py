"""Binary-document conversion behind cairn[ingest] (FR-003, D-002)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("pymupdf4llm", reason="cairn[ingest] extra not installed")
pytest.importorskip("mammoth", reason="cairn[ingest] extra not installed")

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.adapters import FedBinaryAdapter
from cairn.knowledge.ingest.convert import _EXTRA_MISSING, convert_document


@pytest.fixture
def feed_root():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _text_pdf(path: Path, text: str) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()


def _minimal_docx(path: Path, text: str) -> None:
    """Hand-build the smallest valid .docx (OPC zip) mammoth accepts."""
    import zipfile

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


class TestConvertDocument:
    def test_text_pdf_converts_to_markdown(self, feed_root):
        pdf = feed_root / "spec.pdf"
        _text_pdf(pdf, "Deploy the gateway")
        markdown, reason = convert_document(pdf)
        assert reason is None
        assert "gateway" in markdown.lower()

    def test_missing_extra_and_garbage_skip_with_reasons(self, feed_root, monkeypatch):
        pdf = feed_root / "blank.pdf"
        pdf.write_bytes(b"%PDF-1.4 not a real document")
        markdown, reason = convert_document(pdf)
        assert markdown is None
        assert reason

    def test_unsupported_suffix_skips(self, feed_root):
        png = feed_root / "img.png"
        png.write_bytes(b"\x89PNG")
        markdown, reason = convert_document(png)
        assert markdown is None
        assert reason.startswith("unsupported binary type")

    def test_missing_extra_degrades_to_skip_reason(self, feed_root, monkeypatch):
        pdf = feed_root / "spec.pdf"
        _text_pdf(pdf, "Deploy the gateway")
        # A None entry makes `import pymupdf4llm` raise ImportError inside
        # the converter, simulating the cairn[ingest] extra being absent
        # (TC-020); monkeypatch restores the module afterwards.
        monkeypatch.setitem(sys.modules, "pymupdf4llm", None)
        markdown, reason = convert_document(pdf)
        assert markdown is None
        assert reason == _EXTRA_MISSING


class TestDocxConversion:
    def test_docx_converts_to_markdown(self, feed_root):
        docx = feed_root / "spec.docx"
        _minimal_docx(docx, "Deploy the gateway")
        markdown, reason = convert_document(docx)
        assert reason is None
        assert "gateway" in markdown.lower()

    def test_garbage_docx_skips_with_reason(self, feed_root):
        docx = feed_root / "blank.docx"
        docx.write_bytes(b"not a zip at all")
        markdown, reason = convert_document(docx)
        assert markdown is None
        assert reason.startswith("conversion failed")

    def test_docx_stages_tagged_converted_with_resource(self, feed_root):
        docx = feed_root / "spec.docx"
        _minimal_docx(docx, "Deploy the gateway")
        manifest = run_ingest(
            files=[docx], dirs=[], outbox=feed_root / "outbox"
        )
        assert manifest["counts"]["accepted"] == 1
        row = manifest["rows"][0]
        assert "converted" in row["tags"]
        assert "spec.docx" in row["resource"]
        staged = feed_root / "outbox" / row["staged_path"]
        assert staged.exists()


class TestFedBinaryAdapter:
    def test_converted_doc_carries_converted_origin(self, feed_root):
        pdf = feed_root / "spec.pdf"
        _text_pdf(pdf, "Deploy the gateway")
        docs = list(FedBinaryAdapter([pdf]).iter_docs())
        assert len(docs) == 1
        repo, relpath, text, origin = docs[0]
        assert origin == "converted"
        assert "gateway" in text.lower()

    def test_garbage_pdf_lands_in_skipped(self, feed_root):
        pdf = feed_root / "blank.pdf"
        pdf.write_bytes(b"%PDF-1.4 not a real document")
        adapter = FedBinaryAdapter([pdf])
        assert list(adapter.iter_docs()) == []
        assert adapter.skipped and adapter.skipped[0][1]


class TestConvertedPipeline:
    def test_pdf_stages_tagged_converted_with_resource(self, feed_root):
        pdf = feed_root / "spec.pdf"
        _text_pdf(pdf, "Deploy the gateway")
        manifest = run_ingest(
            files=[pdf], dirs=[], outbox=feed_root / "outbox"
        )

        assert manifest["counts"]["accepted"] == 1
        row = manifest["rows"][0]
        assert "converted" in row["tags"]
        assert "spec.pdf" in row["resource"]
        staged = feed_root / "outbox" / row["staged_path"]
        assert staged.exists()
