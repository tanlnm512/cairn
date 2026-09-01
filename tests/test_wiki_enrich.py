"""Pins for `cairn wiki enrich` and the `wiki-page-enrich` kind (FR-008).

Enrichment contract pinned here (D-021, TC-021..TC-024):

* ``cairn wiki enrich [<page-id>] [--repo R] [--all]`` requires exactly one
  selector -- a page-id argument or ``--all``; ``--repo`` only scopes --
  else exit 1.
* Queueing happens only when the promoted concept at
  ``wiki/pages/{repo}/{page_id}`` is readable; an unpromoted or unknown
  page id exits 1 with nothing queued.
* The queued task carries kind ``wiki-page-enrich`` with resource=page_id
  and facts: ``current_body`` (the page's body), fresh seeds/input_hash/
  repo from the manifest row, and a fresh ``commit_sha``; the row's
  task_id/state update without overwriting its recorded ``commit_sha``.
* A critic-passing completion APPENDS the result -- new sections only,
  ending in their own ``## Sources`` footer -- to the promoted body; the
  page's sources merge old+new entries deduped by entry value; the
  Task-Result sibling records exactly the appended sections; and
  ``facts["current_body"]`` keeps the prior body.
* A critic-failing cycle leaves the page byte-identical, spawns
  ``wiki-page-enrich-revise``, and the chain still drops at
  MAX_REVISE_CYCLES with the page untouched.
* ``--all``/``--repo`` scope one enrichment per promoted page across repos,
  and an in-flight enrich already counts as a live chain, blocking
  duplicate generate queueing (``pipeline._live_task_pages``).

Manifest fixtures are written directly as JSON at
`<knowledge>/_wiki/manifest.json` (schema "cairn-wiki-manifest-2", rows
keyed "{repo}/{page_id}") and promoted articles via ``OKFBundle
.write_concept``; the hermetic ``cli_env`` pattern and the critic-cycle
harness are tests/test_wiki_cli.py, tests/test_wiki_export.py and
tests/test_wiki_promotion.py's. Only the specific CLI module is imported,
never the `cairn.cli` package root (C-04).
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cairn.cli.wiki import wiki
from cairn.llm.tasks import (
    MAX_REVISE_CYCLES,
    TASK_DIR,
    claim_task,
    complete_task,
    create_task,
    get_task,
    list_tasks,
)
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept

MANIFEST_SCHEMA = "cairn-wiki-manifest-2"
REPO = "r"
CRITIC_REPO = "r1"

OLD_SHA = "abc1234a"
FRESH_SHA = "def5678b"
SEEDS = ["src/graph/queries.py"]
INPUT_HASH = "hash-overview"

PRIOR_BODY = (
    "# overview\n\n"
    "The `queries` module lives in `src/graph/queries.py`.\n\n"
    "## Sources\n"
    "- `src/graph/queries.py`\n"
)
NEW_SECTIONS = (
    "## Error Handling\n\n"
    "Failures surface in `src/graph/queries.py`.\n\n"
    "## Sources\n"
    "- `src/graph/queries.py`\n"
    "- `queries`\n"
)
# The promoted page's recorded sources: two entries, the first of which the
# new sections' footer cites again -- the merge must keep it exactly once
# and keep the second, uncited entry.
OLD_SOURCES = [
    {"path": "src/graph/queries.py"},
    {"path": "src/graph/refs.py"},
]
MERGED_SOURCES = [
    {"path": "src/graph/queries.py"},
    {"path": "src/graph/refs.py"},
    {"symbol": "queries"},
]

# A result the critic must fail: the cited file does not resolve in the
# graph and the body has no `## Sources` footer.
FAILING_SECTIONS = "See `src/does_not_exist.py` for the extra sections."
# A minimal critic-passing body for driving queued tasks to done.
PASSING_SECTIONS = (
    "# Follow-up\n\n"
    "Detail in `src/graph/queries.py`.\n\n"
    "## Sources\n"
    "- `src/graph/queries.py`\n"
)


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Hermetic store: cwd in tmp, CAIRN_DB/CAIRN_KNOWLEDGE under tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(tmp_path / "knowledge"))
    knowledge = tmp_path / "knowledge"
    (knowledge / "_tasks").mkdir(parents=True)
    return knowledge


def _bundle(knowledge):
    return OKFBundle(str(knowledge))


def _key(page_id, repo=REPO):
    return f"{repo}/{page_id}"


def _row(page_id, *, state, task_id="", attempts=0):
    """One manifest row: the plan entry plus the D-006 tracking fields."""
    return {
        "page_id": page_id,
        "title": "Wiki page",
        "description": "Describes the module.",
        "module": "some-module",
        "seeds": SEEDS,
        "input_hash": INPUT_HASH,
        "task_id": task_id,
        "state": state,
        "attempts": attempts,
    }


def _write_manifest(knowledge, pages):
    manifest_dir = knowledge / "_wiki"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    doc = {"schema": MANIFEST_SCHEMA, "pages": pages}
    (manifest_dir / "manifest.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8"
    )


def _read_manifest(knowledge):
    return json.loads(
        (knowledge / "_wiki" / "manifest.json").read_text(encoding="utf-8")
    )


def _promote_article(bundle, page_id, *, repo=REPO, body=PRIOR_BODY,
                     sources=None):
    """Write the promoted article so the concept resolves."""
    bundle.write_concept(
        OKFConcept(
            type="Wiki-Article",
            title=f"Wiki page {page_id}",
            body=body,
            concept_id=f"wiki/pages/{repo}/{page_id}",
            tags=[repo, "wiki"],
            sources=sources,
        )
    )


def _enrich(knowledge, *args):
    return CliRunner().invoke(
        wiki, ["enrich", "--knowledge", str(knowledge), *args]
    )


def _fresh_head(monkeypatch, sha):
    """HEAD resolution, read through both seams the enrich queue path may
    use (pipeline's module-level import or utils.git directly)."""
    head = lambda repo, workspace=None: sha  # noqa: E731
    monkeypatch.setattr(
        "cairn.wiki.pipeline.get_repo_head", head, raising=False)
    monkeypatch.setattr("cairn.utils.git.get_repo_head", head, raising=False)


def _enrich_facts(repo=CRITIC_REPO, current_body=PRIOR_BODY):
    """Facts in the enrich queue path's shape (D-021)."""
    return {
        "title": "Wiki page",
        "description": "Describes the module.",
        "module": "some-module",
        "seeds": SEEDS,
        "input_hash": INPUT_HASH,
        "repo": repo,
        "current_body": current_body,
        "commit_sha": FRESH_SHA,
    }


def _seed_graph(conn):
    """Seed a minimal graph for critic validation."""
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '/tmp/r1')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "(1, 'r1', '/tmp/r1/src/graph/queries.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, "
        "line_start, line_end) VALUES (1, 1, 'queries', 'module', 'queries', "
        "1, 100)"
    )
    conn.commit()


def _seed_indexed_repo(conn):
    """One indexed repo ('r') with a single file+symbol: enough for the
    planner to yield an overview page under --pages 1."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/tmp/r')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "(1, 'r', 'src/graph/queries.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'queries', 'module', 'queries', 1, 100)"
    )
    conn.commit()


def _pending_enrich_tasks(bundle):
    return [
        t for t in list_tasks(bundle, status="pending")
        if t.task_kind.startswith("wiki-page-enrich")
    ]


def _page_file(knowledge, repo, page_id):
    return knowledge / "wiki" / "pages" / repo / f"{page_id}.md"


# --- TC-021 (queue half): the enrich task carries the page's facts -----------


def test_enrich_queues_task_with_current_body_and_row_facts(cli_env,
                                                            monkeypatch):
    """TC-021: enriching a promoted page queues one wiki-page-enrich task
    whose facts carry the page's current body, the row's seeds/input_hash/
    repo, and a freshly resolved commit_sha; the manifest row's task_id and
    state move to the enrich task while its recorded commit_sha stays."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    _promote_article(bundle, "overview", body=PRIOR_BODY)
    _write_manifest(knowledge, {
        _key("overview"): {
            **_row("overview", state="promoted", task_id="spent-chain",
                   attempts=1),
            "commit_sha": OLD_SHA,
        },
    })
    _fresh_head(monkeypatch, FRESH_SHA)

    result = _enrich(knowledge, "overview")

    assert result.exit_code == 0, result.output
    queued = list_tasks(bundle, status="pending", kind="wiki-page-enrich")
    assert len(queued) == 1
    task = queued[0]
    assert task.resource == "overview"
    assert task.facts["current_body"] == PRIOR_BODY
    assert task.facts["seeds"] == SEEDS
    assert task.facts["input_hash"] == INPUT_HASH
    assert task.facts["repo"] == REPO
    assert task.facts["commit_sha"] == FRESH_SHA

    on_disk = _read_manifest(knowledge)["pages"][_key("overview")]
    assert on_disk["task_id"] == task.id
    assert on_disk["state"] == "queued"
    assert on_disk["commit_sha"] == OLD_SHA


# --- TC-022: enrich refuses unpromoted/unknown pages --------------------------


def test_enrich_refuses_unpromoted_or_unknown_page_and_queues_nothing(cli_env):
    """TC-022: a page id that was never promoted -- queued in the manifest
    or absent entirely -- is refused with exit 1 and a refusal on stderr,
    and no enrichment task exists afterwards."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    _write_manifest(knowledge, {
        _key("overview"): _row("overview", state="queued",
                               task_id="live-chain", attempts=1),
    })

    for page_id in ("overview", "no-such-page"):
        result = _enrich(knowledge, page_id)
        assert result.exit_code == 1, (page_id, result.output)
        assert result.stderr.strip()
    assert _pending_enrich_tasks(bundle) == []


def test_enrich_requires_exactly_one_selector(cli_env):
    """Exactly one selector is required: no page-id and no --all is a usage
    refusal, and so is a page-id together with --all; nothing is queued."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    _promote_article(bundle, "overview")
    _write_manifest(knowledge, {
        _key("overview"): _row("overview", state="promoted",
                               task_id="spent-chain", attempts=1),
    })

    for args in ((), ("overview", "--all")):
        result = _enrich(knowledge, *args)
        assert result.exit_code == 1, (args, result.output)
        assert result.stderr.strip()
    assert _pending_enrich_tasks(bundle) == []


# --- TC-021 (completion half): critic-passing completion appends --------------


def test_passing_enrich_completion_appends_sections_and_merges_sources(
    cli_env, fresh_db
):
    """TC-021: a critic-passing enrich completion appends its sections to
    the promoted body (prior content stays visible, in order), merges the
    page's sources old+new deduped by entry value, records exactly the
    appended sections in the Task-Result sibling, and keeps the prior body
    in facts['current_body']."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    _seed_graph(fresh_db)
    _promote_article(bundle, "overview", repo=CRITIC_REPO, body=PRIOR_BODY,
                     sources=OLD_SOURCES)
    task = create_task(
        bundle, "wiki-page-enrich", "overview", facts=_enrich_facts()
    )
    claim_task(bundle, task.id, "test-agent")

    outcome = complete_task(bundle, task.id, NEW_SECTIONS, conn=fresh_db)

    assert outcome["promoted"] is True, outcome
    article = bundle.read_concept(f"wiki/pages/{CRITIC_REPO}/overview")
    assert article.body == PRIOR_BODY + "\n\n" + NEW_SECTIONS
    assert article.body.index("The `queries` module lives in") < article.body.index(
        "## Error Handling"
    )
    # Sources: old entries first, the overlapping entry once, the new
    # symbol entry appended.
    assert article.sources == MERGED_SOURCES
    assert sum(
        1 for e in article.sources
        if "src/graph/queries.py" in e.values()
    ) == 1

    result_concept = bundle.read_concept(f"{TASK_DIR}/{task.id}.result")
    assert result_concept.body == NEW_SECTIONS
    assert get_task(bundle, task.id).facts["current_body"] == PRIOR_BODY


# --- TC-023: critic-failing cycles leave the page byte-identical --------------


def test_failing_enrich_completion_leaves_page_byte_identical_and_spawns_revise(
    cli_env, fresh_db
):
    """TC-023: a critic-failing enrich completion appends nothing -- the
    page file is byte-identical afterwards -- and spawns a
    wiki-page-enrich-revise task carrying the errors and parent link."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    _seed_graph(fresh_db)
    _promote_article(bundle, "overview", repo=CRITIC_REPO, body=PRIOR_BODY,
                     sources=OLD_SOURCES)
    page_path = _page_file(knowledge, CRITIC_REPO, "overview")
    before = page_path.read_bytes()
    task = create_task(
        bundle, "wiki-page-enrich", "overview", facts=_enrich_facts()
    )
    claim_task(bundle, task.id, "test-agent")

    outcome = complete_task(bundle, task.id, FAILING_SECTIONS, conn=fresh_db)

    assert outcome["promoted"] is False
    assert outcome["revised"] is True
    assert page_path.read_bytes() == before

    revise = list_tasks(bundle, status="pending",
                        kind="wiki-page-enrich-revise")
    assert len(revise) == 1
    assert revise[0].facts.get("parent_task_id") == task.id
    assert revise[0].facts.get("errors")


def test_failing_enrich_chain_drops_at_max_cycles_leaving_page_unchanged(
    cli_env, fresh_db
):
    """TC-023: the enrich chain inherits the bounded revise cycle -- every
    attempt below the cap spawns exactly one revise, the cap drops the
    chain, and the page file is byte-identical through it all."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    _seed_graph(fresh_db)
    _promote_article(bundle, "overview", repo=CRITIC_REPO, body=PRIOR_BODY,
                     sources=OLD_SOURCES)
    page_path = _page_file(knowledge, CRITIC_REPO, "overview")
    before = page_path.read_bytes()
    current = create_task(
        bundle, "wiki-page-enrich", "overview", facts=_enrich_facts()
    )

    while current.attempt < MAX_REVISE_CYCLES:
        claim_task(bundle, current.id, "test-agent")
        outcome = complete_task(
            bundle, current.id, FAILING_SECTIONS, conn=fresh_db
        )
        assert outcome["revised"] is True
        assert outcome["dropped"] is False
        pending = [
            t for t in list_tasks(bundle, status="pending")
            if t.task_kind.startswith("wiki-page")
        ]
        assert len(pending) == 1
        current = pending[0]

    assert current.attempt == MAX_REVISE_CYCLES
    claim_task(bundle, current.id, "test-agent")
    outcome = complete_task(bundle, current.id, FAILING_SECTIONS, conn=fresh_db)
    assert outcome["promoted"] is False
    assert outcome["revised"] is False
    assert outcome["dropped"] is True
    assert [
        t for t in list_tasks(bundle, status="pending")
        if t.task_kind.startswith("wiki-page")
    ] == []
    assert page_path.read_bytes() == before


# --- TC-024: --all and --repo scope the queue ---------------------------------


def test_enrich_all_then_repo_scopes_the_queue(cli_env, fresh_db):
    """TC-024: --all queues one enrichment per promoted page across all
    repositories; once those complete, --all --repo queues only the named
    repository's pages."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    _seed_graph(fresh_db)
    for repo in ("alpha", "beta"):
        _promote_article(bundle, "overview", repo=repo, body=PRIOR_BODY)
    _write_manifest(knowledge, {
        _key("overview", "alpha"): _row("overview", state="promoted",
                                        task_id="chain-alpha", attempts=1),
        _key("overview", "beta"): _row("overview", state="promoted",
                                       task_id="chain-beta", attempts=1),
    })

    first = _enrich(knowledge, "--all")
    assert first.exit_code == 0, first.output
    pending = _pending_enrich_tasks(bundle)
    assert len(pending) == 2
    assert all(t.resource == "overview" for t in pending)
    assert {t.facts["repo"] for t in pending} == {"alpha", "beta"}

    for t in pending:
        claim_task(bundle, t.id, "test-agent")
        outcome = complete_task(bundle, t.id, PASSING_SECTIONS, conn=fresh_db)
        assert outcome["promoted"] is True, outcome

    second = _enrich(knowledge, "--all", "--repo", "alpha")
    assert second.exit_code == 0, second.output
    pending_after = _pending_enrich_tasks(bundle)
    assert [t.facts["repo"] for t in pending_after] == ["alpha"]
    assert all(t.resource == "overview" for t in pending_after)


# --- guard: an in-flight enrich keeps duplicate generate blocked --------------


def test_inflight_enrich_blocks_duplicate_generate_queueing(cli_env, fresh_db):
    """An in-flight wiki-page-enrich task counts as a live chain: a plain
    generate re-run queues nothing for that page even though its concept is
    unreadable (pipeline._live_task_pages)."""
    from cairn.wiki.catalog import build_page_plan
    from cairn.wiki.pipeline import run_wiki_generate

    knowledge = cli_env
    bundle = _bundle(knowledge)
    _seed_indexed_repo(fresh_db)
    entry = build_page_plan(fresh_db, REPO, pages_cap=1)[0]
    enrich = create_task(
        bundle, "wiki-page-enrich", entry["page_id"],
        facts={"repo": REPO, "input_hash": entry["input_hash"]},
    )
    _write_manifest(knowledge, {
        _key(entry["page_id"]): {
            **_row(entry["page_id"], state="queued", task_id=enrich.id,
                   attempts=1),
            "input_hash": entry["input_hash"],
        },
    })

    result = run_wiki_generate(fresh_db, bundle, REPO, pages_cap=1)

    assert result["queued_task_ids"] == []
    live = [
        t for t in list_tasks(bundle, status="pending")
        if t.task_kind.startswith("wiki-page")
    ]
    assert [t.id for t in live] == [enrich.id]
