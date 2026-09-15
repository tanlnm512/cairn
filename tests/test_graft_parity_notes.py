"""Byte-preserving ``## Notes`` sections across generated knowledge writers."""
from __future__ import annotations

import pytest

from cairn.compass.critic import critic_concept
from cairn.compass.generator import generate_compass
from cairn.llm.tasks import claim_task, complete_task, create_task
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept
from cairn.wiki.generator import generate_wiki_with_critic


REPO = "notes-repo"
VALID_PATH = "src/graph/queries.py"
BOGUS_PATH = "src/notes/does-not-exist.py"
BOGUS_SYMBOL = "MissingNotesSymbol"
NOTES_SECTION = (
    "## Notes\n"
    "- Café preservation includes trailing whitespace.   \n"
    "- Irregular blank lines are intentional.\n"
    "\n"
    "\n"
)
NOTES_BYTES = NOTES_SECTION.encode("utf-8")
ENRICHMENT_SECTION = (
    "## Enrichment\n\n"
    f"Additional detail lives in `{VALID_PATH}`.\n\n"
    "## Sources\n"
    f"- `{VALID_PATH}`\n"
)


@pytest.fixture
def knowledge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(tmp_path / "knowledge"))
    root = tmp_path / "knowledge"
    (root / "_tasks").mkdir(parents=True)
    return root


def _seed_graph(conn):
    conn.execute(
        "INSERT INTO repos (id, name, path) "
        "VALUES (?, ?, ?)",
        (REPO, REPO, "/tmp/notes-repo"),
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES (1, ?, '/tmp/notes-repo/src/graph/queries.py', 'python')",
        (REPO,),
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, "
        "line_start, line_end) VALUES (1, 1, 'queries', 'module', "
        "'src.graph.queries', 1, 100)"
    )
    conn.commit()


def _add_symbol(conn, symbol_id, name, kind):
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, "
        "line_start, line_end) VALUES (?, 1, ?, ?, ?, 101, 140)",
        (symbol_id, name, kind, f"src.graph.{name}"),
    )
    conn.commit()


def _bundle(knowledge):
    return OKFBundle(str(knowledge))


def _page_bytes(bundle, concept_id):
    return (bundle.root / f"{concept_id}.md").read_bytes()


def _write_concept(bundle, concept):
    bundle.write_concept(concept)
    return _page_bytes(bundle, concept.concept_id)


def test_regeneration_preserves_notes_bytes_in_wiki_and_compass(
    knowledge, fresh_db
):
    """Wiki and compass regeneration replace generated content without
    changing any byte of a human-authored Notes section."""
    bundle = _bundle(knowledge)
    _seed_graph(fresh_db)

    wiki_concept_id = f"reports/architecture/{REPO}"
    _write_concept(
        bundle,
        OKFConcept(
            type="Architecture-Report",
            title=f"{REPO} Architecture",
            description="Generated architecture report",
            resource=REPO,
            concept_id=wiki_concept_id,
            body="# OLD WIKI BODY\n\nGenerated text to replace.\n\n"
            + NOTES_SECTION,
        )
    )
    compass_concept_id = "compass/src-graph-queries"
    _write_concept(
        bundle,
        OKFConcept(
            type="Compass",
            title="queries",
            description="Generated module compass",
            resource="src/graph/queries",
            concept_id=compass_concept_id,
            body="# OLD COMPASS BODY\n\nGenerated text to replace.\n\n"
            + NOTES_SECTION,
        )
    )

    _add_symbol(fresh_db, 2, "RegeneratedWikiClass", "class")
    wiki, _ = generate_wiki_with_critic(REPO, fresh_db, bundle)
    assert len(wiki) == 1
    wiki_bytes = _write_concept(bundle, wiki[0])
    assert wiki_bytes.count(NOTES_BYTES) == 1
    assert b"OLD WIKI BODY" not in wiki_bytes
    assert b"RegeneratedWikiClass" in wiki_bytes

    compass = generate_compass(
        "src/graph/queries", fresh_db, bundle, repo=REPO
    )
    compass_bytes = _write_concept(bundle, compass)
    assert compass_bytes.count(NOTES_BYTES) == 1
    assert b"OLD COMPASS BODY" not in compass_bytes
    assert b"# What Does This Module Do?" in compass_bytes


def test_enrichment_appends_without_rewriting_or_moving_notes_bytes(
    knowledge, fresh_db, monkeypatch
):
    """A promoted page's Notes bytes stay at their original offset while
    enrichment appends new generated sections."""
    bundle = _bundle(knowledge)
    _seed_graph(fresh_db)
    concept_id = f"wiki/pages/{REPO}/overview"
    _write_concept(
        bundle,
        OKFConcept(
            type="Wiki-Article",
            title="Wiki: overview",
            description="Generated wiki article",
            resource="overview",
            tags=[REPO, "wiki"],
            concept_id=concept_id,
            body="## Generated\n\n"
            f"Generated content cites `{VALID_PATH}`.\n\n"
            + NOTES_SECTION,
        )
    )
    before_body = bundle.read_concept(concept_id).body.encode("utf-8")
    notes_body_offset = before_body.find(NOTES_BYTES)
    task = create_task(
        bundle,
        "wiki-page-enrich",
        f"{REPO}/overview",
        facts={"repo": REPO},
    )
    claim_task(bundle, task.id, "test-agent")
    monkeypatch.setattr(
        "cairn.utils.git.get_repo_head",
        lambda repo, workspace=None: "1234567",
    )

    outcome = complete_task(
        bundle, task.id, ENRICHMENT_SECTION, conn=fresh_db
    )

    assert outcome["promoted"] is True, outcome
    after = _page_bytes(bundle, concept_id)
    after_body = bundle.read_concept(concept_id).body.encode("utf-8")
    assert after.count(NOTES_BYTES) == 1
    assert after_body.find(NOTES_BYTES) == notes_body_offset
    assert after_body.find(b"## Enrichment") > notes_body_offset


def test_critic_ignores_notes_references_but_checks_generated_references(
    fresh_db
):
    """Critic file and symbol checks cover generated sections and never
    evaluate references that occur only in Notes."""
    _seed_graph(fresh_db)
    generated_with_notes = OKFConcept(
        type="Wiki-Article",
        title="Notes exclusion",
        description="Critic fixture",
        concept_id="wiki/pages/notes/exclusion",
        body="## Generated\n\n"
        f"Generated content cites `{VALID_PATH}`.\n\n"
        "## Notes\n"
        f"- Human note cites `{BOGUS_PATH}` and `{BOGUS_SYMBOL}`.\n",
    )
    generated_with_bogus_ref = OKFConcept(
        type="Wiki-Article",
        title="Generated failure",
        description="Critic fixture",
        concept_id="wiki/pages/notes/generated-failure",
        body="## Generated\n\n"
        f"Generated content cites `{BOGUS_PATH}` and `{BOGUS_SYMBOL}`.\n\n"
        "## Notes\n"
        "- Human note has no references.\n",
    )

    notes_result = critic_concept(
        generated_with_notes,
        fresh_db,
        section_vocab=("## Generated",),
    )
    generated_result = critic_concept(
        generated_with_bogus_ref,
        fresh_db,
        section_vocab=("## Generated",),
    )

    assert notes_result.errors == []
    assert not any(
        BOGUS_PATH in warning or BOGUS_SYMBOL in warning
        for warning in notes_result.warnings
    )
    assert notes_result.passed is True
    assert any(BOGUS_PATH in error for error in generated_result.errors)
    assert generated_result.passed is False
