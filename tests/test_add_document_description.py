"""The optional description parameter on the add_document write chokepoint."""
from __future__ import annotations

import tempfile
from pathlib import Path

from cairn.knowledge.store import add_document
from cairn.okf.bundle import OKFBundle

# Same secret shape the title-redaction tests in test_redaction_chokepoints.py
# rely on; strip_private_data rewrites it to [REDACTED_SECRET].
_API_KEY = "api_key=sk-1234567890abcdef1234567890abcdef"


def _bundle(root: Path) -> OKFBundle:
    return OKFBundle(str(root / "knowledge"))


def _read_file_text(bundle: OKFBundle, cid: str) -> str:
    """Raw .md content on disk -- the ground truth for 'never reaches disk'."""
    return (bundle.root / f"{cid}.md").read_text(encoding="utf-8")


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


def test_secret_shaped_description_is_redacted():
    """An explicit description is routed through the privacy floor: the
    secret never reaches the stored frontmatter or the .md file."""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _bundle(Path(tmp))
        cid = add_document(
            bundle, title="Title", body="Body.", doc_type="spec",
            description=f"summary {_API_KEY}",
        )
        concept = bundle.read_concept(cid)
        assert "sk-1234567890" not in concept.description
        assert "REDACTED_SECRET" in concept.description
        text = _read_file_text(bundle, cid)
        assert "sk-1234567890" not in text
        assert "REDACTED_SECRET" in text


def test_secret_shaped_title_fallback_description_stays_redacted():
    """No description -> still falls back to the title, which is already
    redacted, so the fallback semantics are unchanged and leak-free."""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _bundle(Path(tmp))
        cid = add_document(
            bundle, title=f"rotation incident {_API_KEY}", body="Body.",
            doc_type="spec",
        )
        concept = bundle.read_concept(cid)
        assert "sk-1234567890" not in concept.description
        assert "REDACTED_SECRET" in concept.description
        assert "sk-1234567890" not in _read_file_text(bundle, cid)
