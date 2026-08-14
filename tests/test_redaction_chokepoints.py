"""Redaction + namespace guards at the STORE/CHOKEPOINT layers (audit 2026-08).

The audit's design principle: privacy/namespace fixes land at the store or
write chokepoint, never at one caller's boundary -- otherwise the next audit
finds the next divergent caller. These tests pin each chokepoint directly:

  F1  knowledge/store.add_document (covers add_workflow + import_directory,
      which both funnel through it) redacts title/body/step-descriptions
      before anything reaches disk or knowledge_embeddings.
  F2  cli memory capture's no-backend fallback redacts the transcript before
      queueing the memory-extract task.
  F3  capture_memory/evolve_memory redact the TITLE (which is duplicated into
      description and slugified into the filename), not just the body.
  F4  metric_buffering._log_metric redacts error_message BEFORE the row is
      buffered (no redaction-after-persistence inversion).
  F5  _log_metric honors CAIRN_TELEMETRY=off (the master kill switch).
  F6  strip_private_data redacts URI credentials (postgres://user:pass@host,
      ...) while keeping scheme+host readable.
  F7  knowledge/store update_status/delete_document refuse out-of-namespace
      concept_ids; the CLI twins render the refusal; the MCP tools' pre-guard
      still wins (double-guarding doesn't break them).
  F8  memory/store.delete_memory refuses concepts resolved outside memory/
      via get_memory's raw-path fallback.
  F9  complete_task strips result bodies for memory-* task kinds only.
  F10 record_reference(s_batch) redact + truncate the raw query context.
"""
from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner

from cairn.cli import main
from cairn.graph.schema import _apply_schema
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


# Secret-shaped probes. Each matches a pattern in memory/privacy.py's catalog
# (or, for _PG_DSN, the F6 URI-credential pattern), so a missing gate lets the
# raw value through verbatim.
_BEARER = "Bearer abcdefghijklmnopqrstuvwxyz1234567890abcd"
_API_KEY = "api_key=sk-1234567890abcdef1234567890abcdef"
_PG_DSN = "postgres://admin:S3cr3tP4ssw0rdXy9@db-prod"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bundle(tmp_path):
    return OKFBundle(str(tmp_path / "knowledge"))


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    yield conn
    conn.close()


def _read_file_text(bundle: OKFBundle, cid: str) -> str:
    """Raw .md content on disk -- the ground truth for 'never reaches disk'."""
    return (bundle.root / f"{cid}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# F1: knowledge store redaction at the add_document chokepoint
# ---------------------------------------------------------------------------


class TestKnowledgeStoreRedaction:
    def test_add_document_redacts_title_and_body(self, bundle):
        """Secrets in title/body never reach the concept, the frontmatter, or
        the concept_id/filename (slug is derived AFTER redaction)."""
        from cairn.knowledge.store import add_document

        cid = add_document(
            bundle,
            title=f"Deploy broke after rotating {_API_KEY}",
            body=f"Rollback plan: the key {_API_KEY} was pasted in the ticket.",
            doc_type="spec",
        )
        concept = bundle.read_concept(cid)
        assert "sk-1234567890" not in concept.title
        assert "REDACTED_SECRET" in concept.title
        # description mirrors the title -- must be redacted too.
        assert "sk-1234567890" not in concept.description
        assert "sk-1234567890" not in concept.body
        assert "REDACTED_SECRET" in concept.body
        # Slug/filename derived from the redacted title.
        assert "sk-1234567890" not in cid
        # The raw file on disk (body + frontmatter) never holds the secret.
        assert "sk-1234567890" not in _read_file_text(bundle, cid)

    def test_add_document_preserves_clean_agent_content(self, bundle):
        """Pattern-based redaction is a no-op for legitimate wiki/compass-style
        content -- agent-authored docs are not mangled."""
        from cairn.knowledge.store import add_document

        body = "# Shipping a change\n\nRun `pre-commit run --all-files`, then open a PR.\nSee `docs/contribution-workflow.md`."
        cid = add_document(bundle, title="Contribution workflow", body=body, doc_type="workflow")
        concept = bundle.read_concept(cid)
        assert concept.body == body
        assert concept.title == "Contribution workflow"

    def test_add_workflow_redacts_title_body_and_step_descriptions(self, bundle):
        from cairn.knowledge.workflow import add_workflow

        wid = add_workflow(
            bundle,
            title=f"Release flow {_API_KEY}",
            steps=[
                {"name": "run_tests", "description": f"needs {_API_KEY} in env"},
                {"name": "deploy", "description": "plain step"},
            ],
        )
        concept = bundle.read_concept(wid)
        assert "sk-1234567890" not in concept.title
        assert "sk-1234567890" not in concept.body
        steps = concept.extensions["steps"]
        assert "sk-1234567890" not in str(steps)
        assert "REDACTED_SECRET" in steps[0]["description"]
        # Identifier fields survive verbatim (graph anchors, not free text).
        assert steps[0]["name"] == "run_tests"
        assert steps[1]["description"] == "plain step"
        assert "sk-1234567890" not in _read_file_text(bundle, wid)

    def test_import_directory_redacts_body(self, bundle, tmp_path):
        """Files ingested from disk are user content too -- same chokepoint."""
        from cairn.knowledge.store import import_directory

        src = tmp_path / "src"
        src.mkdir()
        (src / "runbook.md").write_text(
            f"# Runbook\n\nConnect with {_PG_DSN} before restarting.\n",
            encoding="utf-8",
        )
        imported = import_directory(bundle, str(src), doc_type="spec")
        assert imported, "fixture: the .md file must import"
        text = _read_file_text(bundle, imported[0])
        assert "S3cr3tP4ssw0rdXy9" not in text
        assert "REDACTED_SECRET" in text
        # Host survives (debuggable), credential does not.
        assert "db-prod" in text


# ---------------------------------------------------------------------------
# F2: cli memory capture fallback queues a redacted transcript
# ---------------------------------------------------------------------------


class TestMemoryCaptureFallbackRedaction:
    def test_queued_transcript_is_redacted(self, tmp_path, monkeypatch):
        """No backend -> the memory-extract task is queued with the transcript
        in facts; the task .md (body + extensions) must not hold the secret."""
        knowledge = tmp_path / "knowledge"
        db_path = tmp_path / "graph.db"
        monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
        monkeypatch.delenv("CAIRN_LLM_BACKEND", raising=False)

        transcript = f'[{{"role": "user", "text": "auth failed for {_BEARER}"}}]'
        result = CliRunner().invoke(
            main,
            [
                "memory", "capture",
                "--session-transcript", transcript,
                "--session-id", "s-test",
                "--db", str(db_path),
                "--knowledge", str(knowledge),
            ],
        )
        assert result.exit_code == 0, result.output

        from cairn.llm.tasks import list_tasks

        tasks = list_tasks(OKFBundle(str(knowledge)), kind="memory-extract")
        assert tasks, "fixture: the fallback must queue a memory-extract task"
        task = tasks[-1]
        assert "abcdefghijklmnopqrstuvwxyz1234" not in task.facts.get("transcript", "")
        assert "REDACTED_SECRET" in task.facts["transcript"]
        # And the rendered task .md body (facts are echoed into it) is clean.
        raw = (knowledge / "_tasks" / f"{task.id}.md").read_text(encoding="utf-8")
        assert "abcdefghijklmnopqrstuvwxyz1234" not in raw

    def test_subprocess_fallback_queues_redacted_transcript(
        self, tmp_path, monkeypatch
    ):
        """CAIRN_LLM_BACKEND=droid with no droid CLI -> SubprocessBackend
        falls back to FileQueueBackend carrying the RAW transcript.

        The CLI's own strip only runs on the no-backend branch; this path
        reaches create_task unredacted, so the task-creation chokepoint must
        scrub it (the codepath-divergence bug class audit F2 targeted).
        Drives SubprocessBackend directly (the CLI wrapper would block on
        FileQueueBackend's 600s completion poll) with the poll disabled.
        """
        from cairn.llm.client import SubprocessBackend

        knowledge = tmp_path / "knowledge"
        monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
        # A real agent CLI may exist on this machine (droid/claude are common
        # dev tools); point PATH at an empty dir so the subprocess spawn
        # deterministically raises FileNotFoundError -> clean fallback.
        empty_bin = tmp_path / "emptybin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))

        bundle = OKFBundle(str(knowledge))
        backend = SubprocessBackend(bundle, cli="droid")
        backend._fallback.max_wait = 0.0  # create the task, skip the poll

        transcript = f'[{{"role": "user", "text": "auth failed for {_BEARER}"}}]'
        out = backend.extract(transcript)
        assert out == [], "fixture: no agent ran, so extraction yields nothing"

        from cairn.llm.tasks import list_tasks

        tasks = list_tasks(bundle, kind="memory-extract")
        assert tasks, "fixture: the subprocess fallback must queue a task"
        task = tasks[-1]
        assert "abcdefghijklmnopqrstuvwxyz1234" not in task.facts.get(
            "transcript", ""
        ), "raw secret reached the persisted task facts via the fallback path"
        assert "REDACTED_SECRET" in task.facts["transcript"]
        raw = (knowledge / "_tasks" / f"{task.id}.md").read_text(encoding="utf-8")
        assert "abcdefghijklmnopqrstuvwxyz1234" not in raw


# ---------------------------------------------------------------------------
# F3: memory titles redacted at capture/evolve
# ---------------------------------------------------------------------------


class TestMemoryTitleRedaction:
    def test_capture_memory_redacts_title(self, db, bundle):
        """A secret-shaped title is redacted in title, description (the
        duplicate), and the slugified concept_id/filename."""
        from cairn.memory.promotion import capture_memory

        r = capture_memory(
            db, bundle, type_="mistake",
            title=f"rotation incident {_API_KEY}",
            body="clean body", confidence=0.9,
        )
        stored = bundle.read_concept(r["path"])
        assert "sk-1234567890" not in stored.title
        assert "sk-1234567890" not in stored.description
        assert "REDACTED_SECRET" in stored.title
        assert "sk-1234567890" not in r["path"]
        assert "sk-1234567890" not in _read_file_text(bundle, r["path"])

    def test_evolve_memory_redacts_new_title(self, db, bundle):
        from cairn.memory.promotion import capture_memory, evolve_memory

        r1 = capture_memory(db, bundle, type_="pattern", title="stable",
                            body="v1", confidence=0.8)
        r2 = evolve_memory(
            db, bundle, r1["path"],
            new_title=f"revised {_API_KEY}",
            new_body="v2",
        )
        assert r2 is not None
        stored = bundle.read_concept(r2["path"])
        assert "sk-1234567890" not in stored.title
        assert "sk-1234567890" not in stored.description
        assert "REDACTED_SECRET" in stored.title


# ---------------------------------------------------------------------------
# F4 + F5: metric_buffering._log_metric write-path redaction + telemetry gate
# ---------------------------------------------------------------------------


class TestMetricBufferingGates:
    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        from cairn.mcp_server import metric_buffering as mb

        with mb._METRIC_LOCK:
            mb._METRIC_BUFFER.clear()
        mb._conn_factory = None
        mb._METRIC_FLUSHER_STARTED = True  # keep the flusher thread unstarted
        monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
        monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
        yield
        with mb._METRIC_LOCK:
            mb._METRIC_BUFFER.clear()

    def test_error_message_redacted_at_write(self):
        """A token-bearing exception message is scrubbed BEFORE the row is
        buffered -- no redaction-after-persistence inversion."""
        from cairn.mcp_server import metric_buffering as mb

        mb._log_metric("explore", 12.0, "error",
                       f"connection refused for {_BEARER}")
        row = list(mb._METRIC_BUFFER)[0]
        assert "abcdefghijklmnopqrstuvwxyz1234" not in (row[5] or "")
        assert "REDACTED_SECRET" in row[5]

    def test_error_message_redaction_survives_flush(self, fresh_db):
        """The stored tool_metrics row (post-flush) never holds the secret."""
        from cairn.mcp_server import metric_buffering as mb

        class _Unclosable:
            def __init__(self, real):
                self._real = real

            def executemany(self, sql, params):
                return self._real.executemany(sql, params)

            def commit(self):
                return self._real.commit()

            def close(self):
                pass

        mb.configure_conn(lambda: _Unclosable(fresh_db))
        mb._log_metric("explore", 12.0, "error", f"boom {_PG_DSN}")
        mb._flush_metrics()
        row = fresh_db.execute(
            "SELECT error_message FROM tool_metrics"
        ).fetchone()
        assert "S3cr3tP4ssw0rdXy9" not in row["error_message"]
        assert "REDACTED_SECRET" in row["error_message"]

    def test_error_message_truncated_after_redaction(self):
        """The 500-char cap still applies, computed on the redacted text."""
        from cairn.mcp_server import metric_buffering as mb

        long_err = "x" * 900 + f" tail {_BEARER}"
        mb._log_metric("tool", 1.0, "error", long_err)
        row = list(mb._METRIC_BUFFER)[0]
        assert row[5] is not None and len(row[5]) <= 500

    def test_ok_path_still_buffers(self):
        from cairn.mcp_server import metric_buffering as mb

        mb._log_metric("tool", 1.0, "ok")
        assert len(mb._METRIC_BUFFER) == 1

    def test_telemetry_off_skips_metric_row(self, monkeypatch):
        """CAIRN_TELEMETRY=off is the master kill switch: tool_metrics rows
        must not be recorded (audit F5)."""
        from cairn.mcp_server import metric_buffering as mb

        monkeypatch.setenv("CAIRN_TELEMETRY", "off")
        mb._log_metric("tool", 1.0, "ok")
        mb._log_metric("tool", 1.0, "error", "boom")
        assert len(mb._METRIC_BUFFER) == 0, (
            "CAIRN_TELEMETRY=off must gate tool_metrics, not just events"
        )

    @pytest.mark.parametrize("value", ["on", "local", "1"])
    def test_telemetry_non_off_still_records(self, monkeypatch, value):
        from cairn.mcp_server import metric_buffering as mb

        monkeypatch.setenv("CAIRN_TELEMETRY", value)
        mb._log_metric("tool", 1.0, "ok")
        assert len(mb._METRIC_BUFFER) == 1


# ---------------------------------------------------------------------------
# F6: URI-credential redaction in strip_private_data
# ---------------------------------------------------------------------------


class TestUriCredentialRedaction:
    @pytest.mark.parametrize(
        "uri,host_hint",
        [
            ("postgres://admin:S3cr3tP4ssw0rdXy9@db", "db"),
            ("postgresql://svc:hunter2@db.internal:5432/app", "db.internal"),
            ("mysql://root:pw@localhost", "localhost"),
            ("redis://:pass@cache", "cache"),
            ("mongodb+srv://u:secret@cluster.mongodb.net", "cluster.mongodb.net"),
            ("amqp://guest:guest@mq:5672/vhost", "mq"),
            ("https://user:pass@example.com/path", "example.com"),
        ],
    )
    def test_credentials_redacted_host_kept(self, uri, host_hint):
        from cairn.memory.privacy import strip_private_data

        out = strip_private_data(f"connect via {uri} now")
        assert "REDACTED_SECRET" in out
        assert host_hint in out, "scheme/host are debug context and survive"
        assert not any(
            cred in out
            for cred in ("S3cr3tP4ssw0rdXy9", "hunter2", ":pw@", ":pass@", "secret@", "user:pass")
        ), f"credential leaked: {out!r}"

    def test_bare_urls_untouched(self):
        from cairn.memory.privacy import strip_private_data

        text = "see https://example.com/docs?a=b and http://host:8080/x"
        assert strip_private_data(text) == text

    def test_redis_empty_user_form(self):
        """redis://:pass@host (empty username) is the canonical Redis DSN."""
        from cairn.memory.privacy import strip_private_data

        out = strip_private_data("redis://:pass@cache")
        assert out == "redis://[REDACTED_SECRET]@cache"


# ---------------------------------------------------------------------------
# F7: knowledge namespace guard at the store chokepoint (+ CLI twins + MCP compat)
# ---------------------------------------------------------------------------


def _write_compass_doc(bundle: OKFBundle, cid: str = "compass/some-module") -> str:
    concept = OKFConcept(
        type="Compass", title="Some Module", description="guide",
        resource="some/module", body="# Some Module\nnavigation guide",
    )
    concept.concept_id = cid
    bundle.write_concept(concept)
    return cid


class TestKnowledgeNamespaceGuard:
    def test_update_status_refuses_out_of_namespace(self, bundle):
        from cairn.knowledge.store import update_status

        cid = _write_compass_doc(bundle)
        with pytest.raises(ValueError, match="outside the knowledge/ namespace"):
            update_status(bundle, cid, "archived")
        # And the compass file was not modified.
        assert "archived" not in _read_file_text(bundle, cid)

    def test_delete_document_refuses_out_of_namespace(self, bundle):
        from cairn.knowledge.store import delete_document

        cid = _write_compass_doc(bundle)
        with pytest.raises(ValueError, match="outside the knowledge/ namespace"):
            delete_document(bundle, cid)
        assert (bundle.root / f"{cid}.md").exists(), "compass file must survive"

    def test_store_ops_still_work_in_namespace(self, bundle):
        from cairn.knowledge.store import add_document, delete_document, update_status

        cid = add_document(bundle, title="Tax policy", body="VAT is 10%",
                           doc_type="business-rule")
        assert update_status(bundle, cid, "archived") is True
        assert delete_document(bundle, cid) is True

    def test_missing_doc_keeps_not_found_semantics(self, bundle):
        """Unresolvable ids return False (no raise) -- the MCP tools' pre-guard
        'not found' branch depends on this."""
        from cairn.knowledge.store import delete_document, update_status

        assert update_status(bundle, "knowledge/spec/nope", "archived") is False
        assert delete_document(bundle, "knowledge/spec/nope") is False

    def test_cli_remove_refuses_compass_doc(self, tmp_path, monkeypatch):
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir(parents=True)
        monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
        bundle = OKFBundle(str(knowledge))
        cid = _write_compass_doc(bundle)

        result = CliRunner().invoke(main, ["knowledge", "remove", cid,
                                           "--db", str(tmp_path / "graph.db")])
        assert result.exit_code != 0, "removing a compass doc must fail"
        assert "Refused" in result.output
        assert (knowledge / f"{cid}.md").exists(), "compass file must survive"

    def test_cli_remove_deletes_real_knowledge_doc(self, tmp_path, monkeypatch):
        from cairn.knowledge.store import add_document

        knowledge = tmp_path / "knowledge"
        knowledge.mkdir(parents=True)
        monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
        db_path = tmp_path / "graph.db"
        monkeypatch.setenv("CAIRN_DB", str(db_path))
        cid = add_document(OKFBundle(str(knowledge)), title="Spec X",
                           body="content", doc_type="spec")

        result = CliRunner().invoke(main, ["knowledge", "remove", cid,
                                           "--db", str(db_path)])
        assert result.exit_code == 0, result.output
        assert "Deleted" in result.output
        assert not (knowledge / f"{cid}.md").exists()

    def test_cli_status_refuses_compass_doc(self, tmp_path, monkeypatch):
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir(parents=True)
        monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
        cid = _write_compass_doc(OKFBundle(str(knowledge)))

        result = CliRunner().invoke(main, ["knowledge", "status", cid, "archived"])
        assert result.exit_code != 0
        assert "Refused" in result.output

    def test_mcp_tools_pre_guard_still_wins(self, tmp_path, monkeypatch):
        """Double-guarding must not break the MCP tools: their own scope check
        returns a refusal STRING (no exception) for an existing out-of-namespace
        doc, and a not-found string for an unresolvable one."""
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir(parents=True)
        monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))

        from cairn.mcp_server.tools_knowledge import knowledge_delete, knowledge_status

        _write_compass_doc(OKFBundle(str(knowledge)))
        out = knowledge_status(doc_id="compass/some-module", new_status="archived")
        assert "Refused" in out  # tool guard, not the store's ValueError
        out = knowledge_delete(doc_id="compass/does-not-exist")
        assert "not found" in out.lower()


# ---------------------------------------------------------------------------
# F8: memory forget namespace guard
# ---------------------------------------------------------------------------


class TestMemoryDeleteNamespaceGuard:
    def test_delete_memory_refuses_out_of_namespace_resolution(self, bundle):
        """get_memory's raw-path fallback can resolve compass concepts; the
        store chokepoint must refuse to unlink them."""
        from cairn.memory.store import delete_memory, get_memory

        cid = _write_compass_doc(bundle, "compass/other-module")
        assert get_memory(bundle, "compass/other-module") is not None  # fixture: fallback resolves
        assert delete_memory(bundle, "compass/other-module") is False
        assert (bundle.root / f"{cid}.md").exists(), "compass file must survive"

    def test_delete_memory_still_deletes_real_memories(self, db, bundle):
        from cairn.memory.promotion import capture_memory
        from cairn.memory.store import delete_memory

        r = capture_memory(db, bundle, type_="decision", title="doomed",
                           body="x", confidence=0.9)
        assert delete_memory(bundle, r["path"]) is True
        assert not (bundle.root / f"{r['path']}.md").exists()

    def test_cli_forget_refuses_compass_doc(self, db, tmp_path):
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir(parents=True)
        bundle = OKFBundle(str(knowledge))
        cid = _write_compass_doc(bundle, "compass/other-module")

        result = CliRunner().invoke(
            main,
            ["memory", "forget", "compass/other-module",
             "--db", str(tmp_path / "graph.db"), "--knowledge", str(knowledge)],
        )
        assert result.exit_code != 0, "forgetting a compass doc must fail"
        assert (knowledge / f"{cid}.md").exists(), "compass file must survive"


# ---------------------------------------------------------------------------
# F9: complete_task strips memory-* result bodies
# ---------------------------------------------------------------------------


class TestTaskResultRedaction:
    def test_memory_extract_result_redacted(self, tmp_path):
        from cairn.llm.tasks import claim_task, complete_task, create_task, read_result

        bundle = OKFBundle(str(tmp_path / "knowledge"))
        task = create_task(bundle, "memory-extract", "session-x")
        claim_task(bundle, task.id, "agent")
        result_with_secret = (
            '{"type": "mistake", "title": "t", '
            f'"body": "leaked {_API_KEY} in logs"}}'
        )
        complete_task(bundle, task.id, result_with_secret, claimer="agent")
        stored = read_result(bundle, task.id)
        assert stored is not None
        assert "sk-1234567890" not in stored
        assert "REDACTED_SECRET" in stored

    def test_non_memory_task_result_untouched(self, tmp_path):
        """The gate is deliberately narrow (memory-* kinds): compass/wiki/flow
        synthesis output is graph-derived and not processed."""
        from cairn.llm.tasks import claim_task, complete_task, create_task, read_result

        bundle = OKFBundle(str(tmp_path / "knowledge"))
        task = create_task(bundle, "wiki", "src/cairn/mod.py")
        claim_task(bundle, task.id, "agent")
        body = "# Architecture\n\nPlain prose, not user content."
        complete_task(bundle, task.id, body, claimer="agent")
        assert read_result(bundle, task.id) == body


# ---------------------------------------------------------------------------
# F10: memory_refs.context sanitized
# ---------------------------------------------------------------------------


class TestRefContextSanitization:
    def test_batch_context_redacted_and_truncated(self, db):
        from cairn.memory.promotion import record_references_batch

        record_references_batch(
            db,
            [("memory/tribal/x", f"who broke auth with {_BEARER}")],
            "s1",
        )
        row = db.execute("SELECT context FROM memory_refs").fetchone()
        assert "abcdefghijklmnopqrstuvwxyz1234" not in row["context"]
        assert "REDACTED_SECRET" in row["context"]

    def test_batch_context_hard_truncated_to_200(self, db):
        from cairn.memory.promotion import record_references_batch

        record_references_batch(db, [("memory/tribal/x", "q" * 500)], "s1")
        row = db.execute("SELECT context FROM memory_refs").fetchone()
        assert len(row["context"]) <= 200

    def test_single_reference_redacted(self, db):
        from cairn.memory.promotion import record_reference

        record_reference(db, "memory/tribal/y", "s1",
                         context=f"searched for {_API_KEY}")
        row = db.execute("SELECT context FROM memory_refs").fetchone()
        assert "sk-1234567890" not in row["context"]

    def test_search_memory_persists_sanitized_query(self, db, bundle):
        """End-to-end: the query search_memory queues verbatim today must land
        redacted via the record_references_batch chokepoint."""
        from cairn.memory.promotion import capture_memory, search_memory

        capture_memory(db, bundle, type_="decision", title="zeta deploy notes",
                       body="content", confidence=0.9)  # high score -> tribal
        search_memory(db, bundle, f"zeta deploy notes {_BEARER}", session_id="s1")
        rows = db.execute("SELECT context FROM memory_refs").fetchall()
        assert rows, "fixture: the tribal hit must record a ref"
        for r in rows:
            assert "abcdefghijklmnopqrstuvwxyz1234" not in r["context"]
