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
