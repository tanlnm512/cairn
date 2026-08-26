"""Binary-document conversion behind cairn[ingest] (FR-003, D-002)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytest.importorskip("pymupdf4llm", reason="cairn[ingest] extra not installed")
pytest.importorskip("mammoth", reason="cairn[ingest] extra not installed")

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.adapters import FedBinaryAdapter
from cairn.knowledge.ingest.convert import convert_document


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
