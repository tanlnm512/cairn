"""Click-level tests for `cairn knowledge` subcommands (add, ingest).

The audit found no test invoking knowledge subcommands through the Click
runner: `add`'s P0 (a dropped --resource decorator crashing every
invocation) shipped because nothing exercised the command layer, and
`ingest`'s traceback-on-bad-path plus discarded verify report likewise
went uncaught. These drive the real command group against hermetic tmp
stores -- resolve_store() reads CAIRN_DB / CAIRN_KNOWLEDGE dynamically,
so pointing both into tmp_path keeps the real ~/.cairn untouched.
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from cairn.cli.knowledge import knowledge


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Hermetic store: cwd in tmp, CAIRN_DB/CAIRN_KNOWLEDGE under tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(tmp_path / "knowledge"))
    return tmp_path


def _stored_docs():
    from cairn.knowledge.store import list_documents
    from cairn.okf.bundle import OKFBundle
    from cairn.paths import resolve_store

    return list_documents(OKFBundle(str(resolve_store().knowledge)))


# --- knowledge add ---------------------------------------------------------


def test_add_happy_path_stores_description_and_resource(cli_env):
    result = CliRunner().invoke(knowledge, [
        "add", "--title", "Refund policy", "--body", "Refunds within 30 days.",
        "--description", "How we handle customer refunds",
        "--resource", "https://confluence.example/x/refund",
    ])
    assert result.exit_code == 0, result.stdout
    assert "Stored: knowledge/" in result.stdout

    docs = _stored_docs()
    assert len(docs) == 1
    doc = docs[0]
    assert doc.title == "Refund policy"
    assert doc.description == "How we handle customer refunds"
    assert doc.resource == "https://confluence.example/x/refund"
    assert "Refunds within 30 days." in doc.body


def test_add_regression_resource_option_stays_optional(cli_env):
    # P0 regression: the --resource decorator was accidentally replaced by
    # the new --description option, so every `knowledge add` invocation
    # crashed with TypeError (missing positional argument 'resource').
    result = CliRunner().invoke(knowledge, [
        "add", "--title", "No resource here", "--body", "Body text.",
    ])
    assert result.exit_code == 0, result.stdout
    assert "Stored: knowledge/" in result.stdout
    assert len(_stored_docs()) == 1


def test_add_without_file_or_body_errors_cleanly(cli_env):
    result = CliRunner().invoke(knowledge, ["add", "--title", "Only a title"])
    assert result.exit_code == 1
    assert "--file or --body required" in result.stderr
    assert "Traceback" not in result.stderr
    assert _stored_docs() == []


def test_add_missing_file_errors_cleanly(cli_env):
    bad = str(cli_env / "does-not-exist.md")
    result = CliRunner().invoke(knowledge, [
        "add", "--title", "Ghost doc", "--file", bad,
    ])
    assert result.exit_code == 1
    # Clean, actionable message naming the bad path -- not a traceback.
    assert "Error:" in result.stderr
    assert bad in result.stderr
    assert "Traceback" not in result.stderr
    assert _stored_docs() == []


# --- knowledge ingest ------------------------------------------------------


def test_ingest_missing_file_is_a_clean_error(cli_env):
    bad = str(cli_env / "does-not-exist.md")
    result = CliRunner().invoke(knowledge, [
        "ingest", "--file", bad, "--outbox", str(cli_env / "outbox"),
    ])
    assert result.exit_code == 1
    # Clean, actionable message naming the bad path -- not a traceback.
    assert "Error:" in result.stderr
    assert bad in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_ingest_empty_dir_dry_run_stages_zero(cli_env):
    empty = cli_env / "docs"
    empty.mkdir()
    result = CliRunner().invoke(knowledge, [
        "ingest", "--dir", str(empty), "--outbox", str(cli_env / "outbox"),
    ])
    assert result.exit_code == 0, result.stdout
    assert "Staged 0 document(s), skipped 0." in result.stdout


def test_ingest_flag_writes_and_prints_verify_legs(cli_env, hash_backend):
    # hash_backend: execute_manifest embeds when a backend is available;
    # the dep-free hash embedder keeps that leg fast and model-free.
    docs = cli_env / "docs"
    docs.mkdir()
    (docs / "spec.md").write_text(
        "---\ntitle: Widget spec\nstatus: accepted\n---\n"
        "# Widget spec\n\nWidgets must be greased weekly.\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(knowledge, [
        "ingest", "--dir", str(docs), "--outbox", str(cli_env / "outbox"),
        "--ingest",
    ])
    assert result.exit_code == 0, result.stdout
    assert "Wrote 1 document(s) to the store" in result.stdout
    # The verify legs of the executor report are printed, not discarded.
    assert "store_count: 1" in result.stdout
    assert "count_ok: True" in result.stdout
    assert "smoke_search_hit: True" in result.stdout
    assert len(_stored_docs()) == 1


def test_ingest_flag_exits_nonzero_when_a_verify_leg_fails(cli_env, monkeypatch):
    # The executor contract is the report dict; stub it (runtime patch only,
    # executor.py itself is untouched) so count_ok lands as an explicit False.
    import cairn.knowledge.ingest.executor as executor_mod

    def _failing_report(manifest, conn):
        return {
            "written": ["knowledge/spec/x"], "embedded": None,
            "accepted": 1, "skipped": 0,
            "store_count": 0, "expected_count": 1,
            "count_ok": False, "smoke_search_hit": False,
        }

    monkeypatch.setattr(executor_mod, "execute_manifest", _failing_report)
    docs = cli_env / "docs"
    docs.mkdir()
    (docs / "spec.md").write_text(
        "---\ntitle: Widget spec\nstatus: accepted\n---\nBody.\n", encoding="utf-8"
    )
    result = CliRunner().invoke(knowledge, [
        "ingest", "--dir", str(docs), "--outbox", str(cli_env / "outbox"),
        "--ingest",
    ])
    assert result.exit_code == 1
    assert "count_ok: False" in result.stdout
    assert "verification failed" in result.stderr


# --- overwrite visibility, import dir errors, staging errors ---------------


def test_add_same_title_and_type_reports_the_replacement(cli_env):
    args = ["add", "--title", "Refund policy", "--body", "v1 body."]
    first = CliRunner().invoke(knowledge, args)
    assert first.exit_code == 0, first.stdout
    assert "replaced" not in first.stdout

    second = CliRunner().invoke(knowledge, [
        "add", "--title", "Refund policy", "--body", "v2 body.",
    ])
    assert second.exit_code == 0, second.stdout
    assert (
        "(replaced an existing document with the same title and doc_type)"
        in second.stdout
    )
    docs = _stored_docs()
    assert len(docs) == 1
    assert "v2 body." in docs[0].body


def test_import_missing_directory_is_a_clean_error(cli_env):
    missing = cli_env / "no-such-docs"
    result = CliRunner().invoke(knowledge, ["import", str(missing)])
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert str(missing) in result.stderr
    assert "Traceback" not in result.stderr
    assert "Imported" not in result.stdout


def test_import_file_path_is_a_clean_error(cli_env):
    plain_file = cli_env / "notes.md"
    plain_file.write_text("not a directory\n", encoding="utf-8")
    result = CliRunner().invoke(knowledge, ["import", str(plain_file)])
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert str(plain_file) in result.stderr
    assert "Traceback" not in result.stderr


def test_ingest_staging_valueerror_is_a_clean_error(cli_env, monkeypatch):
    # The staging cross-check raises ValueError; it must render as a clean
    # CLI error like the FileNotFoundError leg, never a raw traceback.
    import cairn.knowledge.ingest as ingest_mod

    def _cross_check_boom(files, dirs, outbox, include_drafts, repos):
        raise ValueError(
            "staging cross-check failed for a.md: expected title='A' "
            "type='Knowledge-spec', staged title='B' type='Knowledge-spec'"
        )

    monkeypatch.setattr(ingest_mod, "run_ingest", _cross_check_boom)
    docs = cli_env / "docs"
    docs.mkdir()
    result = CliRunner().invoke(knowledge, [
        "ingest", "--dir", str(docs), "--outbox", str(cli_env / "outbox"),
    ])
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "staging cross-check failed" in result.stderr
    assert "Traceback" not in result.stderr


# --- store guards before the DB is opened ----------------------------------


def test_search_on_uninitialized_store_is_a_clean_empty_result(cli_env):
    result = CliRunner().invoke(knowledge, [
        "search", "refund", "--db", str(cli_env / "no-such-dir" / "kg"),
    ])
    assert result.exit_code == 0, result.stdout
    assert "No knowledge documents matching 'refund'." in result.stdout
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_remove_on_uninitialized_store_is_a_clean_not_found(cli_env):
    result = CliRunner().invoke(knowledge, [
        "remove", "knowledge/spec/ghost",
        "--db", str(cli_env / "no-such-dir" / "kg"),
    ])
    assert result.exit_code == 1
    assert "Not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_impact_on_uninitialized_store_is_a_clean_empty_result(cli_env):
    result = CliRunner().invoke(knowledge, [
        "impact", "refund", "--db", str(cli_env / "no-such-dir" / "kg"),
    ])
    assert result.exit_code == 0, result.stdout
    assert "No knowledge documents matching 'refund'." in result.stdout
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_embed_on_fresh_store_initializes_cleanly(cli_env, hash_backend):
    result = CliRunner().invoke(knowledge, [
        "embed", "--db", str(cli_env / "graph.db"),
    ])
    assert result.exit_code == 0, result.stdout
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_embed_missing_db_parent_is_a_clean_error(cli_env, hash_backend):
    result = CliRunner().invoke(knowledge, [
        "embed", "--db", str(cli_env / "no-such-dir" / "kg"),
    ])
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "Traceback" not in result.stderr


# --- impact graph-bridge rendering ------------------------------------------


def _add_doc_with_repo(cli_env):
    added = CliRunner().invoke(knowledge, [
        "add", "--title", "Refund policy", "--body", "Refunds within 30 days.",
        "--affects", "ghost-repo",
    ])
    assert added.exit_code == 0, added.stdout


def test_impact_lists_repos_without_graph_edges(cli_env):
    _add_doc_with_repo(cli_env)
    result = CliRunner().invoke(knowledge, [
        "impact", "refund", "--db", str(cli_env / "graph.db"),
    ])
    assert result.exit_code == 0, result.stdout
    assert "ghost-repo → no cross-repo dependencies in the graph." in result.stdout


def test_impact_reports_graph_bridge_errors(cli_env, monkeypatch):
    import cairn.knowledge.search as search_mod

    monkeypatch.setattr(
        search_mod, "_graph_bridge",
        lambda conn, repos: {
            repo: {"error": "repo not found in graph"} for repo in repos
        },
    )
    _add_doc_with_repo(cli_env)
    result = CliRunner().invoke(knowledge, [
        "impact", "refund", "--db", str(cli_env / "graph.db"),
    ])
    assert result.exit_code == 0, result.stdout
    assert "ghost-repo → graph lookup failed: repo not found in graph" in result.stdout


# --- knowledge workflow add/sync CLI surface --------------------------------


def test_workflow_add_steps_file_missing_is_a_clean_error(cli_env):
    bad = str(cli_env / "missing.yaml")
    result = CliRunner().invoke(knowledge, [
        "workflow", "add", "--title", "Deploy flow", "--steps-file", bad,
    ])
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert bad in result.stderr
    assert "Traceback" not in result.stderr


def test_workflow_add_steps_file_non_dict_entries_is_a_clean_error(cli_env):
    steps_file = cli_env / "steps.yaml"
    steps_file.write_text("- just a plain string step\n", encoding="utf-8")
    result = CliRunner().invoke(knowledge, [
        "workflow", "add", "--title", "Deploy flow",
        "--steps-file", str(steps_file),
    ])
    assert result.exit_code == 1
    assert "must contain a YAML/JSON list of step dicts" in result.stderr
    assert "Traceback" not in result.stderr


def test_workflow_sync_dry_run_reads_store_bundle(cli_env):
    # The bundle must come from resolve_store (CAIRN_KNOWLEDGE), not the
    # --db-derived default, so a workflow added via the CLI is visible.
    steps_file = cli_env / "steps.yaml"
    steps_file.write_text(
        "- name: Deploy\n  description: Ship it\n"
        "  symbol: deployHotfix\n  file: src/Deploy.kt\n",
        encoding="utf-8",
    )
    added = CliRunner().invoke(knowledge, [
        "workflow", "add", "--title", "Deploy flow",
        "--steps-file", str(steps_file),
    ])
    assert added.exit_code == 0, added.stdout

    result = CliRunner().invoke(knowledge, [
        "workflow", "sync", "--all", "--dry-run",
        "--db", str(cli_env / "graph.db"),
    ])
    assert result.exit_code == 0, result.stdout
    assert "Deploy flow" in result.stdout
    assert "1 workflow(s) with stale steps:" in result.stdout


def test_workflow_sync_on_fresh_store_is_a_clean_noop(cli_env):
    result = CliRunner().invoke(knowledge, [
        "workflow", "sync", "--all", "--dry-run",
        "--db", str(cli_env / "no-such-dir" / "kg"),
    ])
    assert result.exit_code == 0, result.stdout
    assert "No workflows to sync." in result.stdout
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_workflow_sync_rejects_ref_with_all(cli_env):
    result = CliRunner().invoke(knowledge, [
        "workflow", "sync", "deploy-flow", "--all",
        "--db", str(cli_env / "graph.db"),
    ])
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "not both" in result.stderr
    assert "Traceback" not in result.stderr
