import tempfile

from cairn.memory.store import consolidate_memories
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


def test_memory_consolidation():
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle = OKFBundle(tmpdir)

        c1 = OKFConcept(type="RawMemory", title="Fix DB Lock", body="Use WAL mode for sqlite", concept_id="memory/raw/fix-db-lock-1")
        c2 = OKFConcept(type="RawMemory", title="Fix DB Lock", body="Set busy_timeout to 5000ms", concept_id="memory/raw/fix-db-lock-2")
        bundle.write_concept(c1)
        bundle.write_concept(c2)

        consolidated = consolidate_memories(bundle)
        assert consolidated == 2

        tribal = bundle.list_concepts(prefix="memory/tribal")
        assert len(tribal) == 1
        merged = bundle.read_concept(tribal[0])
        assert "WAL mode" in merged.body
        assert "busy_timeout" in merged.body
