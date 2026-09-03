"""Contract and regression tests for wiki task kinds and the revise cycle.

FR-002 pins the ``_output_spec`` contract for the four wiki kinds —
``wiki-page``, ``wiki-page-revise``, ``wiki-catalog``, ``wiki-catalog-revise``
(revise kinds are derived by appending ``-revise``, so all four must be
registered). The page spec requires a markdown article ending in a
``## Sources`` footer, forbids references outside the graph, and carries
Mermaid-fence instructions only when ``facts.diagrams`` is set (facts pass
through ``create_task`` verbatim; only memory-* kinds are stripped).
Assertions pin stable substrings ("## Sources", "Mermaid",
"outside the graph"), not full sentences.

FR-004 guards the existing kind-agnostic revise cycle for the wiki kind: a
critic-failing ``wiki-page`` completion spawns a bounded ``wiki-page-revise``
task carrying ``errors`` + ``parent_task_id``; the chain drops at
``MAX_REVISE_CYCLES`` with ``dropped: True`` and nothing promoted.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cairn.llm.tasks import (
    MAX_REVISE_CYCLES,
    _output_spec,
    _render_body,
    claim_task,
    complete_task,
    create_task,
    get_task,
    list_tasks,
)
from cairn.okf.bundle import OKFBundle

PAGE_KINDS = (
    "wiki-page",
    "wiki-page-revise",
    "wiki-catalog",
    "wiki-catalog-revise",
)

# A result the critic must fail: the cited file does not resolve in the graph
# (error) and the body has none of the scored section headings (quality 0.0).
_FAILING_RESULT = "See `src/does_not_exist.py` for the page contents."


def _seed_graph(conn: sqlite3.Connection) -> None:
    """Seed a minimal graph for critic validation."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '/tmp/r1')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "(1, 'r1', '/tmp/r1/src/graph/queries.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'queries', 'module', 'queries', 1, 100)"
    )
    conn.commit()


def _create_bundle(tmp_path: Path) -> OKFBundle:
    """Create a test bundle in the temp directory."""
    knowledge_dir = tmp_path / ".knowledge"
    knowledge_dir.mkdir(exist_ok=True)
    tasks_dir = knowledge_dir / "_tasks"
    tasks_dir.mkdir(exist_ok=True)
    return OKFBundle(knowledge_dir)


def _pending_wiki_page_tasks(bundle: OKFBundle) -> list:
    return [
        t
        for t in list_tasks(bundle, status="pending")
        if t.task_kind.startswith("wiki-page")
    ]


class TestWikiOutputSpecRegistration:
    """FR-002: every wiki kind resolves to a real output spec.

    FR-005: any kind whose name starts with ``wiki-page`` is served the full
    wiki spec (Sources-footer requirement intact), never the default string.
    """

    def test_all_four_wiki_kinds_have_registered_specs(self):
        fallback = _output_spec("__not_a_registered_kind__")
        for kind in PAGE_KINDS:
            assert _output_spec(kind) != fallback, (
                f"{kind} has no output spec registered"
            )

    def test_wiki_page_spec_requires_sources_footer_and_in_graph_refs(self):
        spec = _output_spec("wiki-page")
        assert "## Sources" in spec
        assert "outside the graph" in spec
        # Base spec: no diagrams requested, so no Mermaid instructions.
        assert "Mermaid" not in spec

    def test_wiki_page_revise_spec_requires_sources_footer_and_in_graph_refs(self):
        spec = _output_spec("wiki-page-revise")
        assert "## Sources" in spec
        assert "outside the graph" in spec

    def test_wiki_page_prefix_kinds_serve_the_full_wiki_spec(self):
        fallback = _output_spec("__not_a_registered_kind__")
        for kind in ("wiki-page-enrich", "wiki-page-enrich-revise"):
            spec = _output_spec(kind)
            assert spec != fallback, f"{kind} falls back to the default spec"
            assert "## Sources" in spec
            assert "outside the graph" in spec

    def test_wiki_page_prefix_kinds_render_the_wiki_spec_in_the_task_body(
        self, tmp_path
    ):
        bundle = _create_bundle(tmp_path)
        for kind in ("wiki-page-enrich", "wiki-page-enrich-revise"):
            task = create_task(
                bundle,
                kind,
                "overview",
                facts={"repo": "r1", "input_hash": "h1"},
            )
            body = _render_body(get_task(bundle, task.id))
            assert "## Sources" in body, f"{kind} body lacks the Sources spec"
            assert "Process per the cairn skill." not in body


class TestMermaidGating:
    """FR-002 (US2 AC3): Mermaid instructions ride facts.diagrams only."""

    def test_diagrams_fact_passes_through_and_gates_mermaid_instructions(
        self, tmp_path
    ):
        bundle = _create_bundle(tmp_path)

        with_diagrams = create_task(
            bundle,
            "wiki-page",
            "overview",
            facts={"input_hash": "h1", "diagrams": True},
        )
        persisted = get_task(bundle, with_diagrams.id)
        # Facts pass through verbatim (only memory-* kinds are stripped).
        assert persisted.facts["diagrams"] is True
        body = _render_body(persisted)
        assert "## Sources" in body
        assert "Mermaid" in body

        without_diagrams = create_task(
            bundle,
            "wiki-page",
            "overview",
            facts={"input_hash": "h2"},
        )
        body = _render_body(get_task(bundle, without_diagrams.id))
        assert "## Sources" in body
        assert "Mermaid" not in body


class TestWikiPageReviseCycle:
    """FR-004 guards: critic-fail branch stays bounded for the wiki kind."""

    def test_failing_wiki_page_completion_spawns_revise_carrying_errors_and_parent(
        self, fresh_db, tmp_path
    ):
        _seed_graph(fresh_db)
        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            "wiki-page",
            "r1/overview",
            facts={"repo": "r1", "input_hash": "h1"},
        )
        claim_task(bundle, task.id, "test-agent")

        outcome = complete_task(bundle, task.id, _FAILING_RESULT, conn=fresh_db)

        assert set(outcome.keys()) == {
            "task_id",
            "promoted",
            "revised",
            "dropped",
            "errors",
            "quality",
        }
        assert outcome["task_id"] == task.id
        assert outcome["promoted"] is False
        assert outcome["revised"] is True
        assert outcome["dropped"] is False
        assert outcome["errors"]

        revise_tasks = list_tasks(
            bundle, status="pending", kind="wiki-page-revise"
        )
        assert len(revise_tasks) == 1
        revise = revise_tasks[0]
        assert revise.facts.get("parent_task_id") == task.id
        assert revise.facts.get("errors")

        result_concept = bundle.read_concept(task.result_concept_id)
        assert result_concept.extensions.get("critic_status") == "failed"
        # Nothing is promoted for the failing attempt.
        assert bundle.list_concepts(prefix="wiki/") == []

    def test_wiki_page_chain_drops_at_max_revise_cycles(self, fresh_db, tmp_path):
        _seed_graph(fresh_db)
        bundle = _create_bundle(tmp_path)
        current = create_task(
            bundle,
            "wiki-page",
            "r1/overview",
            facts={"repo": "r1", "input_hash": "h1"},
        )
        assert current.attempt == 1

        # Attempts below the cap keep spawning exactly one pending revise.
        while current.attempt < MAX_REVISE_CYCLES:
            claim_task(bundle, current.id, "test-agent")
            outcome = complete_task(
                bundle, current.id, _FAILING_RESULT, conn=fresh_db
            )
            assert outcome["revised"] is True
            assert outcome["dropped"] is False
            pending = _pending_wiki_page_tasks(bundle)
            assert len(pending) == 1
            current = pending[0]

        assert current.attempt == MAX_REVISE_CYCLES
        claim_task(bundle, current.id, "test-agent")
        outcome = complete_task(bundle, current.id, _FAILING_RESULT, conn=fresh_db)
        assert outcome["promoted"] is False
        assert outcome["revised"] is False
        assert outcome["dropped"] is True
        assert outcome["errors"]
        assert _pending_wiki_page_tasks(bundle) == []
        assert bundle.list_concepts(prefix="wiki/") == []


# --------------------------------------------------------------------------
# FR-005: the critic reports each unresolved path once per completion,
# regardless of how many citation forms mention it.
# --------------------------------------------------------------------------


class TestCriticDedupePerCompletion:
    """FR-005: one unresolved-path error per distinct dead path."""

    def test_path_cited_in_prose_and_footer_reported_once(
        self, fresh_db, tmp_path
    ):
        _seed_graph(fresh_db)
        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            "wiki-page",
            "overview",
            facts={"repo": "r1", "input_hash": "hash-overview"},
        )
        claim_task(bundle, task.id, "test-agent")

        result = (
            "# Overview\n\n"
            "The page describes `src/does_not_exist.py` in detail.\n\n"
            "## Sources\n"
            "- `src/does_not_exist.py`\n"
            "- `src/also_missing.py`\n"
        )
        outcome = complete_task(bundle, task.id, result, conn=fresh_db)

        errors = outcome["errors"]
        # Exactly one error line per distinct dead path -- the prose backtick,
        # the footer backtick, and the footer entry collapse into one.
        assert len(errors) == 2, f"expected one error per dead path, got {errors}"
        assert sum(1 for e in errors if "src/does_not_exist.py" in e) == 1
        assert sum(1 for e in errors if "src/also_missing.py" in e) == 1
        # Rejection itself is not weakened by the dedupe.
        assert outcome["promoted"] is False
        assert outcome["revised"] is True
        assert bundle.list_concepts(prefix="wiki/") == []
        revise_tasks = list_tasks(
            bundle, status="pending", kind="wiki-page-revise"
        )
        assert len(revise_tasks) == 1
        assert revise_tasks[0].facts.get("errors")


# --------------------------------------------------------------------------
# FR-003: Sources-footer parsing, the critic section vocabulary, and the
# Wiki-Article promotion branch.
#
# The parser module does not exist yet, so it is imported inside the tests:
# a module-level import would fail collection and take the FR-002/FR-004
# tests above down with it.
# --------------------------------------------------------------------------

from cairn.compass.critic import CriticResult, critic_concept
from cairn.okf.concept import OKFConcept

# A passing wiki result: graph-verified refs, no compass section headings,
# and a `## Sources` footer citing only the seeded file. Under the wiki
# section vocabulary this must promote (D-001); under the default compass
# vocabulary it scores 0.0, which is why the promotion branch must pass
# the wiki vocab to the critic.
_PASSING_RESULT = (
    "# Overview\n\n"
    "The `queries` module lives in `src/graph/queries.py`.\n\n"
    "## Sources\n"
    "- `src/graph/queries.py`\n"
)

_COMPASS_SHAPED_RESULT = (
    "# What Does This Module Do?\nSee `src/graph/queries.py`.\n"
    "# Common Modification Patterns\n...\n"
    "# Build-Failure Patterns\n...\n"
    "# Cross-Module Dependencies\n...\n"
    "# Tribal Knowledge\n...\n"
)


def _result_concept(body: str) -> OKFConcept:
    return OKFConcept(type="Task-Result", concept_id="_tasks/x.result", body=body)


class TestSourcesFooterParser:
    """FR-003: cairn.wiki.sources parses `## Sources` footer entries."""

    def test_parses_list_and_inline_link_forms_in_order(self):
        from cairn.wiki.sources import parse_sources_footer

        body = (
            "# Overview\n\n"
            "Prose cites `src/graph/queries.py` before the footer.\n\n"
            "## Sources\n"
            "- `src/graph/queries.py`\n"
            "- [`refs.py`](src/cairn/refs.py#L1)\n"
            "[critic.py](src/cairn/compass/critic.py#L38)\n"
        )
        assert parse_sources_footer(body) == [
            "src/graph/queries.py",
            "src/cairn/refs.py",
            "src/cairn/compass/critic.py",
        ]

    def test_body_without_footer_yields_no_entries(self):
        from cairn.wiki.sources import parse_sources_footer

        assert parse_sources_footer("# Overview\nNo footer here.\n") == []

    def test_footer_without_entries_yields_no_entries(self):
        from cairn.wiki.sources import parse_sources_footer

        assert parse_sources_footer("# Overview\n\n## Sources\n") == []


class TestResolveSources:
    """FR-003: footer entries resolve against the graph; unresolved = error."""

    def test_file_and_symbol_entries_resolve(self, fresh_db):
        from cairn.wiki.sources import resolve_sources

        _seed_graph(fresh_db)
        resolved, errors = resolve_sources(
            ["src/graph/queries.py", "queries"], fresh_db
        )
        assert resolved == ["src/graph/queries.py", "queries"]
        assert errors == []

    def test_unresolved_entry_is_reported_as_error(self, fresh_db):
        from cairn.wiki.sources import resolve_sources

        _seed_graph(fresh_db)
        resolved, errors = resolve_sources(
            ["src/graph/queries.py", "src/does_not_exist.py"], fresh_db
        )
        assert resolved == ["src/graph/queries.py"]
        assert errors
        assert any("src/does_not_exist.py" in e for e in errors)


class TestCriticSectionVocab:
    """FR-003 (D-001): optional section_vocab; default stays compass-identical."""

    def test_default_vocab_bit_identical_for_existing_callers(self, fresh_db):
        _seed_graph(fresh_db)
        concept = _result_concept(_COMPASS_SHAPED_RESULT)

        by_default = critic_concept(concept, fresh_db)
        with_explicit_none = critic_concept(concept, fresh_db, section_vocab=None)

        assert with_explicit_none == by_default
        assert by_default.passed is True
        assert by_default.quality_score == 1.0
        assert by_default.errors == []

    def test_wiki_vocab_footer_present_scores_full_and_passes(self, fresh_db):
        _seed_graph(fresh_db)

        result = critic_concept(
            _result_concept(_PASSING_RESULT),
            fresh_db,
            section_vocab=("## Sources",),
        )

        assert result.passed is True
        assert result.quality_score == 1.0
        assert result.errors == []

    def test_wiki_vocab_missing_footer_scores_zero_and_fails(self, fresh_db):
        _seed_graph(fresh_db)

        result = critic_concept(
            _result_concept(
                "Refs `src/graph/queries.py` with no footer to speak of."
            ),
            fresh_db,
            section_vocab=("## Sources",),
        )

        assert result.passed is False
        assert result.quality_score == 0.0
        assert result.errors == []

    def test_critic_result_shape_unchanged(self):
        import dataclasses

        assert [f.name for f in dataclasses.fields(CriticResult)] == [
            "errors",
            "warnings",
            "quality_score",
            "passed",
        ]


class TestWikiPagePromotionBranch:
    """FR-003: a critic-passing wiki-page completion promotes a Wiki-Article."""

    def test_passing_overview_completion_promotes_wiki_article(
        self, fresh_db, tmp_path
    ):
        _seed_graph(fresh_db)
        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            "wiki-page",
            "overview",
            facts={"repo": "r1", "input_hash": "hash-overview"},
        )
        claim_task(bundle, task.id, "test-agent")

        outcome = complete_task(bundle, task.id, _PASSING_RESULT, conn=fresh_db)

        assert outcome["promoted"] is True
        assert outcome["revised"] is False
        assert outcome["dropped"] is False
        assert outcome["errors"] == []
        assert outcome["quality"] == 1.0

        article = bundle.read_concept("wiki/pages/r1/overview")
        assert article.type == "Wiki-Article"
        assert article.tags == ["r1", "wiki"]
        # sources frontmatter: one entry per verified footer entry, naming it.
        assert article.sources and len(article.sources) == 1
        assert "src/graph/queries.py" in set(article.sources[0].values())
        assert article.extensions.get("page_id") == "overview"
        assert article.extensions.get("input_hash") == "hash-overview"
        assert article.extensions.get("task_id") == task.id

    def test_revise_kind_promotes_under_module_page_id(self, fresh_db, tmp_path):
        _seed_graph(fresh_db)
        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            "wiki-page-revise",
            "src-cairn-graph",
            facts={"repo": "r1", "input_hash": "hash-graph"},
        )
        claim_task(bundle, task.id, "test-agent")

        outcome = complete_task(bundle, task.id, _PASSING_RESULT, conn=fresh_db)

        assert outcome["promoted"] is True
        article = bundle.read_concept("wiki/pages/r1/src-cairn-graph")
        assert article.type == "Wiki-Article"
        assert article.extensions.get("page_id") == "src-cairn-graph"


# --------------------------------------------------------------------------
# FR-010: promoted pages are first-class knowledge (no new search code).
#
# Each test drives one promotion through the wiki-page branch, then asserts
# the already-wired surfaces reach it: bundle-wide search with no area filter
# (the `cairn wiki search` path) and the compass wiki layer. Frontmatter must
# survive a file round trip unchanged apart from the populated `sources`.
# --------------------------------------------------------------------------

from cairn.compass.router import route_query


def _promote_overview(fresh_db, bundle: OKFBundle, result: str = _PASSING_RESULT):
    """Drive one critic-passing wiki-page completion to promotion."""
    task = create_task(
        bundle,
        "wiki-page",
        "overview",
        facts={"repo": "r1", "input_hash": "hash-overview"},
    )
    claim_task(bundle, task.id, "test-agent")
    outcome = complete_task(bundle, task.id, result, conn=fresh_db)
    assert outcome["promoted"] is True
    return task


class TestPromotedArticleIsSearchable:
    """FR-010: bundle-wide search (no area filter) reaches the article."""

    def test_search_for_body_topic_finds_promoted_article(
        self, fresh_db, tmp_path
    ):
        _seed_graph(fresh_db)
        bundle = _create_bundle(tmp_path)
        _promote_overview(fresh_db, bundle)
        promoted = bundle.read_concept("wiki/pages/r1/overview")

        hits = bundle.search("queries")

        assert any(
            c.type == "Wiki-Article" and c.title == promoted.title for c in hits
        )


class TestPromotedArticleSurfacesInCompassRouting:
    """FR-010: the compass router's wiki layer names the article."""

    # bundle.search matches the whole query string, so the routed phrase must
    # appear in the article body for the wiki layer to fire.
    _ROUTING_RESULT = (
        "# Overview\n\n"
        "How does the `queries` module work? It lives in `src/graph/queries.py`.\n\n"
        "## Sources\n"
        "- `src/graph/queries.py`\n"
    )

    def test_routed_query_wiki_layer_names_promoted_article(
        self, fresh_db, tmp_path
    ):
        _seed_graph(fresh_db)
        bundle = _create_bundle(tmp_path)
        _promote_overview(fresh_db, bundle, result=self._ROUTING_RESULT)
        promoted = bundle.read_concept("wiki/pages/r1/overview")

        route = route_query("how does", fresh_db, bundle)

        assert "wiki" in route["results"]
        assert promoted.title in route["results"]["wiki"]


class TestPromotedArticleFrontmatterFidelity:
    """FR-010: the article round-trips as a plain concept plus `sources`."""

    def test_frontmatter_survives_file_round_trip(self, fresh_db, tmp_path):
        _seed_graph(fresh_db)
        bundle = _create_bundle(tmp_path)
        _promote_overview(fresh_db, bundle)

        article_path = (
            tmp_path / ".knowledge" / "wiki" / "pages" / "r1" / "overview.md"
        )
        on_disk = OKFConcept.from_file(str(article_path))
        article_path.write_text(on_disk.to_markdown(), encoding="utf-8")
        reparsed = OKFConcept.from_file(str(article_path))

        assert reparsed.concept_id == on_disk.concept_id
        assert reparsed.concept_id.endswith("wiki/pages/r1/overview")
        assert reparsed.type == on_disk.type == "Wiki-Article"
        assert reparsed.title == on_disk.title
        assert reparsed.description == on_disk.description
        assert reparsed.resource == on_disk.resource
        assert reparsed.tags == on_disk.tags
        assert reparsed.timestamp == on_disk.timestamp
        assert reparsed.body == on_disk.body
        # The one frontmatter addition: `sources` populated, verbatim across
        # the round trip.
        assert reparsed.sources == on_disk.sources
        assert reparsed.sources
        assert reparsed.extensions == on_disk.extensions
        assert {"page_id", "repo", "input_hash", "task_id"} <= set(
            reparsed.extensions
        )
        # Re-rendering is a fixed point: no field added or dropped.
        assert reparsed.to_markdown() == on_disk.to_markdown()


# --------------------------------------------------------------------------
# FR-003 (US2 AC1, D-016): the promotion branch records the workspace HEAD
# sha the page was generated from as a fifth extensions key, copied from
# facts exactly like input_hash.
# --------------------------------------------------------------------------


class TestPromotionRecordsCommitSha:
    """FR-003 (two-kind contract edition): provenance is resolved at
    completion time — the HEAD sha rides the article's extensions alone,
    never the task facts or the manifest."""

    def test_passing_completion_resolves_head_into_extensions(
        self, fresh_db, tmp_path, monkeypatch
    ):
        _seed_graph(fresh_db)
        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            "wiki-page",
            "r1/overview",
            facts={"repo": "r1", "input_hash": "hash-overview"},
        )
        claim_task(bundle, task.id, "test-agent")
        monkeypatch.setattr(
            "cairn.utils.git.get_repo_head",
            lambda repo, workspace=None: "abc1234",
        )

        outcome = complete_task(bundle, task.id, _PASSING_RESULT, conn=fresh_db)

        assert outcome["promoted"] is True
        article = bundle.read_concept("wiki/pages/r1/overview")
        assert article.extensions.get("commit_sha") == "abc1234"
        assert {
            "page_id",
            "repo",
            "input_hash",
            "task_id",
            "commit_sha",
        } <= set(article.extensions)
        # Plan provenance is not content provenance: refine_catalog stays
        # in the task facts where it belongs.
        assert "refine_catalog" not in article.extensions
        assert "commit_sha" not in task.facts

    def test_completion_without_a_resolvable_sha_still_promotes_without_one(
        self, fresh_db, tmp_path, monkeypatch
    ):
        _seed_graph(fresh_db)
        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            "wiki-page",
            "r1/overview",
            facts={"repo": "r1", "input_hash": "hash-overview"},
        )
        claim_task(bundle, task.id, "test-agent")
        monkeypatch.setattr(
            "cairn.utils.git.get_repo_head",
            lambda repo, workspace=None: None,
        )

        outcome = complete_task(bundle, task.id, _PASSING_RESULT, conn=fresh_db)

        assert outcome["promoted"] is True
        article = bundle.read_concept("wiki/pages/r1/overview")
        assert {"page_id", "repo", "input_hash", "task_id"} <= set(
            article.extensions
        )
        # Unknown HEAD: the key is absent, never None-valued (TC-008).
        assert "commit_sha" not in article.extensions
