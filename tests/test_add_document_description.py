"""The optional description parameter on the add_document write chokepoint."""
from __future__ import annotations

import tempfile
from pathlib import Path

from cairn.knowledge.store import add_document
from cairn.okf.bundle import OKFBundle


def _bundle(root: Path) -> OKFBundle:
    return OKFBundle(str(root / "knowledge"))


def test_passed_description_lands_in_the_concept():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _bundle(Path(tmp))
        cid = add_document(
            bundle, title="Title", body="Body.", doc_type="spec",
            description="A real one-line summary.",
        )
        concept = bundle.read_concept(cid)
        assert concept.description == "A real one-line summary."
        assert concept.title == "Title"


def test_none_description_keeps_today_behavior():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _bundle(Path(tmp))
        cid = add_document(
            bundle, title="Title", body="Body.", doc_type="spec",
        )
        concept = bundle.read_concept(cid)
        assert concept.description == "Title"
