import tempfile
from pathlib import Path
import pytest

from codegraph.okf.bundle import OKFBundle
from codegraph.knowledge.store import add_document
from codegraph.okf.concept import OKFConcept


def test_okf_bundle_path_traversal_blocked():
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle = OKFBundle(tmpdir)

        # Attempt read outside root
        with pytest.raises(ValueError, match="escapes bundle root"):
            bundle.read_concept("../../victim")

        # Attempt write outside root
        c = OKFConcept(type="spec", concept_id="../../victim_concept", title="Payload", body="Attacker content")
        with pytest.raises(ValueError, match="escapes bundle root"):
            bundle.write_concept(c)


def test_knowledge_store_slugifies_doc_type():
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle = OKFBundle(tmpdir)
        concept_id = add_document(
            bundle,
            title="Safe Title",
            body="Safe Body",
            doc_type="../../victim",
        )
        assert concept_id.startswith("knowledge/victim/")
        assert ".." not in concept_id

        # Verify concept can be read back safely
        concept = bundle.read_concept(concept_id)
        assert concept.title == "Safe Title"
