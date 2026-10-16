"""Tests for memory lifecycle features: supersession, decay, and auto-capture.

Covers the three features adapted from the agentmemory comparison:
  1. Supersession: insert-time near-dup detection, evolve_memory, version
     chains, search filtering of superseded memories.
  2. Exponential freshness + reinforcement signal: weighted scoring with
     exp(-λ·age) decay and reinforcement boost from recall refs.
  3. Privacy filter: strip_private_data regex redaction (used by the
     post_tool_failure auto-capture hook).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.graph.schema import _apply_schema, get_db
from cairn.memory.privacy import strip_private_data
from cairn.memory.promotion import (
    batch_critic,
    capture_memory,
    decay,
    evolve_memory,
    record_references_batch,
    search_memory,
    tribal_digest,
)
from cairn.memory.scoring import (
    WEIGHTS,
    _freshness,
    _reinforcement,
    compute_score,
    score_memory,
)
from cairn.memory.store import (
    create_memory,
    list_memories,
    store_memory,
    tier_for_score,
)
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


# ---------------------------------------------------------------------------
# Fixtures
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


# ---------------------------------------------------------------------------
# FEATURE 1: Supersession
# ---------------------------------------------------------------------------

class TestSupersessionInsert:
    """record_memory auto-supersedes near-duplicates of the same type."""

    def test_same_title_same_type_supersedes(self, db, bundle):
        """Recording a memory with the same title+type supersedes the old."""
        r1 = capture_memory(db, bundle, type_="decision", title="Use JWT", body="v1", confidence=0.8)
        r2 = capture_memory(db, bundle, type_="decision", title="Use JWT", body="v2 revised", confidence=0.85)

        assert r2["superseded"] is not None
        # Old memory is marked superseded.
        old = bundle.read_concept(r1["path"])
        assert old.extensions["memory_is_latest"] is False
        assert old.extensions["memory_superseded_by"] == r2["path"]
        # New memory is latest and chains the old.
        new = bundle.read_concept(r2["path"])
        assert new.extensions["memory_is_latest"] is True
        assert r1["path"] in new.extensions["memory_supersedes"]

    def test_different_type_does_not_supersede(self, db, bundle):
        """Same title but different type creates a parallel memory."""
        capture_memory(db, bundle, type_="decision", title="X", body="d", confidence=0.8)
        r2 = capture_memory(db, bundle, type_="pattern", title="X", body="p", confidence=0.8)
        assert r2["superseded"] is None

    def test_version_chain_inherited(self, db, bundle):
        """A 3rd revision inherits the full chain, not just the immediate predecessor."""
        capture_memory(db, bundle, type_="decision", title="auth", body="v1", confidence=0.8)
        capture_memory(db, bundle, type_="decision", title="auth", body="v2", confidence=0.85)
        r3 = capture_memory(db, bundle, type_="decision", title="auth", body="v3", confidence=0.9)
        final = bundle.read_concept(r3["path"])
        chain = final.extensions["memory_supersedes"]
        assert len(chain) == 2
        # Chain is relative paths, not absolute.
        assert all(not c.startswith("/") for c in chain)

    def test_unreadable_memory_concept_is_skipped(self, db, bundle):
        """A corrupt memory file (invalid encoding) is skipped during
        supersession lookup, not propagated as a crash into capture_memory."""
        r1 = capture_memory(
            db, bundle, type_="decision", title="corrupt me", body="v1", confidence=0.8
        )
        # Corrupt the stored memory file so read_concept raises on decode.
        (bundle.root / f"{r1['path']}.md").write_bytes(b"\xff\xfe not utf-8 \x80")

        r2 = capture_memory(
            db, bundle, type_="decision", title="still alive", body="v2", confidence=0.8
        )
        assert r2["path"], "capture must survive an unreadable sibling concept"

class TestSupersessionSearch:
    """search_memory hides superseded memories by default."""

    def test_default_hides_superseded(self, db, bundle):
        capture_memory(db, bundle, type_="decision", title="unique decision alpha", body="v1", confidence=0.8)
        capture_memory(db, bundle, type_="decision", title="unique decision alpha", body="v2", confidence=0.85)
        results = search_memory(db, bundle, "unique decision alpha", session_id="t")
        assert len(results) == 1
        assert results[0].extensions.get("memory_is_latest", True) is not False

    def test_include_superseded_shows_all(self, db, bundle):
        capture_memory(db, bundle, type_="decision", title="unique decision beta", body="v1", confidence=0.8)
        capture_memory(db, bundle, type_="decision", title="unique decision beta", body="v2", confidence=0.85)
        results = search_memory(db, bundle, "unique decision beta", session_id="t", include_superseded=True)
        assert len(results) >= 2

    def test_old_memory_without_is_latest_treated_as_latest(self, db, bundle):
        """Pre-feature memories (no memory_is_latest field) default to latest."""
        # Manually create a memory without the new fields.
        concept = OKFConcept(
            type="Tribal-decision", title="legacy", body="legacy memory",
            concept_id="memory/tribal/legacy-abc123",
            extensions={"memory_tier": "tribal", "memory_type": "decision"},
        )
        bundle.write_concept(concept)
        results = search_memory(db, bundle, "legacy", session_id="t")
        assert any(r.title == "legacy" for r in results)


class TestEvolveMemory:
    """evolve_memory creates explicit revisions."""

    def test_evolve_creates_new_version(self, db, bundle):
        r1 = capture_memory(db, bundle, type_="pattern", title="evolve test", body="original", confidence=0.7)
        result = evolve_memory(db, bundle, r1["path"], new_body="revised body")
        assert result is not None
        assert result["superseded"] is not None
        old = bundle.read_concept(r1["path"])
        assert old.extensions["memory_is_latest"] is False

    def test_evolve_nonexistent_returns_none(self, db, bundle):
        result = evolve_memory(db, bundle, "memory/tribal/nonexistent", new_body="x")
        assert result is None

    def test_evolve_redacts_secret_in_new_body(self, db, bundle):
        """A secret in an evolved body never reaches disk verbatim.

        Regression for the evolve_memory redaction bypass: capture_memory
        redacts, but evolve_memory once passed new_body straight through.
        """
        secret = "api_key=sk-1234567890abcdef1234567890abcdef"
        r1 = capture_memory(
            db, bundle, type_="pattern", title="evolve redact",
            body="original clean body", confidence=0.7,
        )
        result = evolve_memory(
            db, bundle, r1["path"],
            new_body=f"Updated: rotated {secret} during deploy.",
        )
        assert result is not None
        stored = bundle.read_concept(result["path"])
        assert secret not in stored.body
        assert "sk-1234567890" not in stored.body
        assert "REDACTED_SECRET" in stored.body


# ---------------------------------------------------------------------------
# FEATURE 2: Exponential freshness + reinforcement
# ---------------------------------------------------------------------------

class TestScoringWeights:
    """The 5-signal weight table sums to 1.0 and includes reinforcement."""

    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

class TestExponentialFreshness:
    """_freshness uses exp(-λ·age), not linear decay."""

    def test_brand_new_memory_freshness_near_one(self):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        c = OKFConcept(type="Tribal-decision", title="x", body="x", timestamp=ts,
                       extensions={"memory_type": "decision"})
        assert _freshness(c) > 0.99

    def test_decision_at_90_days_near_half(self):
        """90-day-old decision ≈ 0.5 (half-life)."""
        ts = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        c = OKFConcept(type="Tribal-decision", title="x", body="x", timestamp=ts,
                       extensions={"memory_type": "decision"})
        f = _freshness(c)
        assert 0.4 < f < 0.6

    def test_workaround_decays_slower_than_decision(self):
        """270-day half-life vs 90-day: at 90 days, workaround is fresher."""
        ts = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        dec = OKFConcept(type="Tribal-decision", title="x", body="x", timestamp=ts,
                         extensions={"memory_type": "decision"})
        work = OKFConcept(type="Tribal-workaround", title="x", body="x", timestamp=ts,
                          extensions={"memory_type": "workaround"})
        assert _freshness(work) > _freshness(dec)

    def test_manual_doc_never_ages(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
        c = OKFConcept(type="Tribal-decision", title="x", body="x", timestamp=ts,
                       extensions={"memory_type": "decision", "doc_source": "manual"})
        assert _freshness(c) == 1.0

    def test_naive_timestamp_degrades_to_neutral(self):
        """A hand-authored naive timestamp scores neutral instead of raising."""
        c = OKFConcept(type="Tribal-decision", title="x", body="x",
                       timestamp="2024-01-01T00:00:00",
                       extensions={"memory_type": "decision"})
        assert _freshness(c) == 0.5


class TestNaiveTimestampDecayDegradation:
    """decay() degrades a naive hand-authored timestamp to age 0 instead of
    crashing the pass with an aware-minus-naive TypeError."""

    def test_decay_survives_naive_timestamp(self, db, bundle):
        from cairn.memory.promotion import decay
        from cairn.memory.store import create_memory, store_memory

        mem = create_memory(type_="pattern", title="naive raw note",
                            body="x", confidence=0.5)
        cid = store_memory(mem, bundle, tier="raw")
        raw = bundle.read_concept(cid)
        raw.timestamp = "2024-01-01T00:00:00"
        bundle.write_concept(raw)

        result = decay(bundle, raw_max_days=7, conn=db)

        assert result["expired_raw"] == 0
        assert len(bundle.list_concepts(prefix="memory/raw")) == 1


class TestReinforcement:
    """_reinforcement rewards memories that have been recalled."""

    def test_never_recalled_is_zero(self, db, bundle):
        r = capture_memory(db, bundle, type_="decision", title="unrecalled", body="x", confidence=0.8)
        concept = bundle.read_concept(r["path"])
        assert _reinforcement(concept, db) == 0.0

    def test_recalled_is_positive(self, db, bundle):
        r = capture_memory(db, bundle, type_="decision", title="recalled mem", body="x", confidence=0.8)
        search_memory(db, bundle, "recalled", session_id="s1")
        concept = bundle.read_concept(r["path"])
        assert _reinforcement(concept, db) > 0.0

    def test_reinforcement_saturates_at_one(self, db, bundle):
        """Many recent recalls cap at 1.0."""
        r = capture_memory(db, bundle, type_="decision", title="saturate test", body="x", confidence=0.8)
        # Record many refs with recent (1-day-ago) timestamps directly.
        # Use 1 day ago so days_since > 0 (the guard skips same-day accesses).
        from datetime import datetime, timezone
        ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        for i in range(20):
            db.execute(
                "INSERT INTO memory_refs (id, memory_path, session_id, referenced_at, context) VALUES (?, ?, ?, ?, ?)",
                (f"id{i}", r["path"], f"s{i}", ts, ""),
            )
        db.commit()
        concept = bundle.read_concept(r["path"])
        # 20 refs at 1 day ago: boost = 0.3 * 20 * (1/1.0) = 6.0, capped at 1.0
        assert _reinforcement(concept, db) == 1.0


class TestSevenSignalScore:
    """score_memory returns per-signal values including reinforcement."""

    def test_signals_include_reinforcement(self, db, bundle):
        r = capture_memory(db, bundle, type_="decision", title="sig test", body="x", confidence=0.8)
        concept = bundle.read_concept(r["path"])
        signals = score_memory(concept, db, bundle)
        assert "reinforcement" in signals
        assert "freshness" in signals
        assert "score" in signals

    def test_compute_score_includes_reinforcement(self):
        signals = {
            "graph_verification": 1.0,
            "cross_session_refs_signal": 0.0,
            "agent_confidence": 0.5,
            "critic_score": 0.5,
            "freshness": 1.0,
            "reinforcement": 1.0,
            "authority": 0.5,
        }
        score = compute_score(signals)
        # critic_score/authority keys are ignored (unweighted diagnostics).
        expected = 0.357 * 1.0 + 0.286 * 0.0 + 0.214 * 0.5 + 0.0715 * 1.0 + 0.0715 * 1.0
        assert abs(score - expected) < 1e-6


class TestScoreSpread:
    """The weighted formula spreads scores across cross_session_refs levels
    and separates tribal-promotable memories from raw ones."""

    def test_cross_session_refs_levels_spread_scores(self):
        base = {
            "graph_verification": 1.0,
            "cross_session_refs_signal": 0.0,
            "agent_confidence": 0.5,
            "freshness": 1.0,
            "reinforcement": 0.0,
        }
        scores = [
            round(compute_score({**base, "cross_session_refs_signal": refs}), 3)
            for refs in (0.0, 0.25, 0.5, 0.75, 1.0)
        ]
        assert len(set(scores)) > 2

    def test_promotion_threshold_split(self):
        """High graph_verification + cross_session_refs reaches tribal tier;
        a memory scoring only on agent_confidence does not."""
        memory_a = {
            "graph_verification": 1.0,
            "cross_session_refs_signal": 1.0,
            "agent_confidence": 0.5,
            "freshness": 0.5,
            "reinforcement": 0.0,
        }
        memory_b = {
            "graph_verification": 0.0,
            "cross_session_refs_signal": 0.0,
            "agent_confidence": 0.5,
            "freshness": 0.5,
            "reinforcement": 0.0,
        }
        score_a = compute_score(memory_a)
        score_b = compute_score(memory_b)
        assert score_a - score_b > 0.3
        assert tier_for_score(score_a) == "tribal"
        assert tier_for_score(score_b) != "tribal"


# ---------------------------------------------------------------------------
# FEATURE 3: Privacy filter
# ---------------------------------------------------------------------------

class TestPrivacyFilter:

    def test_strips_api_key(self):
        text = "api_key=sk-1234567890abcdef1234567890abcdef"
        result = strip_private_data(text)
        assert "sk-1234" not in result
        assert "REDACTED_SECRET" in result

    def test_strips_bearer_token(self):
        text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890"
        result = strip_private_data(text)
        assert "Bearer abcdef" not in result
        assert "REDACTED_SECRET" in result

    def test_strips_private_tags(self):
        text = "before <private>secret stuff</private> after"
        result = strip_private_data(text)
        assert "secret stuff" not in result
        assert "REDACTED" in result
        assert "before" in result
        assert "after" in result

    def test_strips_github_token(self):
        text = "token ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
        result = strip_private_data(text)
        assert "ghp_" not in result

    def test_strips_jwt(self):
        text = "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = strip_private_data(text)
        assert "eyJhbGci" not in result

    def test_preserves_normal_text(self):
        text = "The function foo() calls bar() in src/main.py"
        result = strip_private_data(text)
        assert result == text

    def test_strips_multiple_secrets(self):
        text = "key=sk-1234567890abcdef1234567890 token=xoxb-1234567890abcdef"
        result = strip_private_data(text)
        assert "sk-1234" not in result
        assert "xoxb-1234" not in result

    def test_empty_string(self):
        assert strip_private_data("") == ""


# ---------------------------------------------------------------------------
# Regression: capture_memory must redact secrets before persisting (P1).
# The hook path already redacts; this covers every other caller (MCP
# record_memory, CLI).
# ---------------------------------------------------------------------------

class TestCaptureMemoryRedaction:

    def test_secret_in_body_is_redacted_before_storage(self, db, bundle):
        """A secret in the body never reaches disk verbatim."""
        secret = "api_key=sk-1234567890abcdef1234567890abcdef"
        result = capture_memory(
            db, bundle, type_="mistake", title="leaked config",
            body=f"Deploy failed because {secret} was rotated.", confidence=0.8,
        )
        stored = bundle.read_concept(result["path"])
        assert secret not in stored.body
        assert "sk-1234567890" not in stored.body
        assert "REDACTED_SECRET" in stored.body

    def test_non_secret_body_is_preserved(self, db, bundle):
        """Normal technical content is not altered by the redaction floor."""
        body = "ApiFactory.create() builds the client per flavor."
        result = capture_memory(
            db, bundle, type_="pattern", title="client factory",
            body=body, confidence=0.8,
        )
        stored = bundle.read_concept(result["path"])
        assert stored.body == body


# ---------------------------------------------------------------------------
# Regression: promotion lifecycle fixes.
# ---------------------------------------------------------------------------

class _LockedOnceConn:
    """sqlite3 conn proxy whose executemany inserts one row then raises once."""

    def __init__(self, conn):
        self._conn = conn
        self.executemany_calls = 0

    def executemany(self, sql, rows):
        self.executemany_calls += 1
        if self.executemany_calls == 1:
            self._conn.executemany(sql, rows[:1])
            raise sqlite3.OperationalError("database is locked")
        return self._conn.executemany(sql, rows)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class TestPromotionLifecycleRegressions:

    def test_record_references_batch_retry_rolls_back_partial_insert(self, db):
        """A locked mid-batch failure retries cleanly instead of re-inserting fixed PKs."""
        refs = [("memory/tribal/a-1", "query"), ("memory/tribal/b-2", "query")]
        flaky = _LockedOnceConn(db)
        record_references_batch(flaky, refs, "sess-1")
        rows = db.execute("SELECT memory_path FROM memory_refs").fetchall()
        assert sorted(r["memory_path"] for r in rows) == [
            "memory/tribal/a-1", "memory/tribal/b-2",
        ]
        assert flaky.executemany_calls == 2

    def test_batch_critic_persists_decision_in_promotion_history(self, db, bundle):
        """The critic's Decision entry reaches the stored file, not just memory."""
        capture_memory(
            db, bundle, type_="decision", title="critic draft target",
            body="plain body text", confidence=0.1,
        )
        result = batch_critic(db, bundle)
        assert result["processed"] == 1
        assert result["remaining_drafts"] == 1
        mems = list_memories(bundle)
        assert len(mems) == 1
        actions = [h["action"] for h in mems[0].extensions["promotion_history"]]
        assert actions[-1] == "keep_draft"

    def test_batch_critic_db_failure_leaves_no_cross_tier_copy(self, db, bundle):
        """A mid-move DB failure unlinks the new-tier file so the draft stays the sole copy."""
        store_memory(
            create_memory(
                type_="decision", title="critic dup target",
                body="see `src/missing_ref.py`", confidence=0.1,
            ),
            bundle, tier="drafts", conn=db,
        )

        class _ValidityFailConn:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *a, **k):
                if "INSERT INTO memory_validity" in sql:
                    raise sqlite3.OperationalError("attempt to write a readonly database")
                return self._conn.execute(sql, *a, **k)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        with pytest.raises(sqlite3.OperationalError):
            batch_critic(_ValidityFailConn(db), bundle)

        assert len(list_memories(bundle, tier="drafts")) == 1
        assert list_memories(bundle, tier="archived") == []
        assert list_memories(bundle, tier="tribal") == []

        # A healthy retry re-does the move once instead of accumulating copies.
        batch_critic(db, bundle)
        assert list_memories(bundle, tier="drafts") == []
        assert len(list_memories(bundle, tier="archived")) == 1

    def test_batch_critic_renames_embedding_on_tier_move(self, db, bundle):
        """A critic tier move carries the embedding row to the new concept id."""
        cid = store_memory(
            create_memory(
                type_="decision", title="critic embed target",
                body="see `src/missing_ref.py`", confidence=0.1,
            ),
            bundle, tier="drafts", conn=db,
        )
        db.execute(
            "INSERT INTO memory_embeddings"
            " (doc_id, chunk_index, model, dim, vec, chunk, embedded_at)"
            " VALUES (?, 0, 't-model', 1, x'00', 'c', '2026-01-01T00:00:00')",
            (cid,),
        )

        result = batch_critic(db, bundle)

        assert result["dropped"] == 1
        docs = [row["doc_id"] for row in
                db.execute("SELECT doc_id FROM memory_embeddings").fetchall()]
        assert docs and all(d.startswith("memory/archived/") for d in docs)
        assert cid not in docs

    def test_search_survives_corrupt_memory_file_in_broaden(self, db, bundle):
        """A corrupt memory file is skipped by the lexical broaden, not fatal to search."""
        capture_memory(
            db, bundle, type_="decision", title="xyzzy needle",
            body="unique xyzzy content", confidence=0.8,
        )
        broken = bundle.root / "memory" / "raw" / "broken-abc"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.with_suffix(".md").write_bytes(b"\xff\xfe not utf-8")
        results = search_memory(db, bundle, "xyzzy")
        assert [c.title for c in results] == ["xyzzy needle"]

    def test_decay_commits_validity_even_when_reap_fails(self, tmp_path, monkeypatch):
        """Decay's validity projection is committed even when the reap step fails."""
        import cairn.graph.embeddings as emb
        from cairn.graph.schema import get_db

        db_path = tmp_path / "graph.db"
        conn = get_db(str(db_path))
        kb = OKFBundle(str(tmp_path / "knowledge"))
        concept = create_memory(
            type_="decision", title="stale raw memory",
            body="see `src/missing_ref.py`", confidence=0.1,
        )
        concept.timestamp = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        store_memory(concept, kb, tier="raw", conn=conn)

        def _boom(conn, bundle):
            raise RuntimeError("reap exploded")

        monkeypatch.setattr(emb, "reap_orphaned_memory_embeddings", _boom)
        result = decay(kb, conn=conn)
        assert result["expired_raw"] == 1
        other = sqlite3.connect(str(db_path))
        try:
            rows = other.execute("SELECT concept_id FROM memory_validity").fetchall()
        finally:
            other.close()
        # The projection followed the tier move (raw -> archived) and committed
        # even though the reap step failed.
        assert len(rows) == 1 and rows[0][0].startswith("memory/archived/")
        conn.close()

    def test_tribal_digest_rejects_negative_limit(self, bundle):
        """A negative limit fails loudly instead of slicing from the end."""
        with pytest.raises(ValueError):
            tribal_digest(bundle, limit=-1)

    def test_evolve_seeds_refs_baseline(self, db, bundle):
        """An evolved memory seeds memory_refs_baseline like capture does."""
        r1 = capture_memory(
            db, bundle, type_="decision", title="retry policy",
            body="use `src/missing_ref.py`", confidence=0.8,
        )
        result = evolve_memory(
            db, bundle, r1["path"], new_body="use `src/missing_ref.py` (updated)"
        )
        stored = bundle.read_concept(result["path"])
        assert "memory_refs_baseline" in stored.extensions



# ---------------------------------------------------------------------------
# CLI verbs: durability of the write -> embed -> commit -> close sequence
# ---------------------------------------------------------------------------


class TestMemoryCliDurability:
    """CLI memory verbs keep files and DB rows consistent through failures."""

    @pytest.fixture(autouse=True)
    def _hash_backend(self, hash_backend):
        """The CLI record/evolve/capture paths embed; keep them dep-free."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def _store(self, tmp_path):
        db_path = str(tmp_path / "g.db")
        knowledge = str(tmp_path / "k")
        conn = get_db(db_path)
        conn.close()
        return db_path, knowledge

    def test_record_bootstraps_missing_store_dir(self, runner, tmp_path):
        """`memory record` initializes a never-initialized store instead of crashing."""
        db_path = str(tmp_path / "fresh" / "g.db")
        result = runner.invoke(_main(), [
            "memory", "record", "decision", "first memory",
            "--db", db_path, "--knowledge", str(tmp_path / "k"),
        ])
        assert result.exit_code == 0, result.output
        assert Path(db_path).exists()

    def test_evolve_missing_memory_exits_one(self, runner, tmp_path):
        """The evolve not-found path exits 1 like forget/demote."""
        db_path, knowledge = self._store(tmp_path)
        result = runner.invoke(_main(), [
            "memory", "evolve", "memory/tribal/nope",
            "--db", db_path, "--knowledge", knowledge,
        ])
        assert result.exit_code == 1
        assert "Memory not found" in result.output

    def test_evolve_embed_failure_commits_rows(self, runner, tmp_path, monkeypatch):
        """A failed embed keeps the written files and the validity rows consistent."""
        from cairn.graph import embeddings as emb

        db_path, knowledge = self._store(tmp_path)
        bundle = OKFBundle(knowledge)
        conn = get_db(db_path)
        try:
            r = capture_memory(conn, bundle, "decision", "evolve me", "v1", confidence=0.8)
            conn.commit()
        finally:
            conn.close()

        def boom(conn, bundle, ids):
            raise RuntimeError("alias preflight failed")

        monkeypatch.setattr(emb, "embed_memory_concepts", boom)
        result = runner.invoke(_main(), [
            "memory", "evolve", r["path"], "--body", "v2",
            "--db", db_path, "--knowledge", knowledge,
        ])
        assert result.exit_code == 0, result.output
        assert "Embedding failed" in result.output

        ro = get_db(db_path, read_only=True)
        try:
            rows = ro.execute("SELECT concept_id FROM memory_validity").fetchall()
        finally:
            ro.close()
        assert len(rows) == 2

    def test_capture_embed_failure_commits_and_normalizes_type(self, runner, tmp_path, monkeypatch):
        """A failed embed still commits; off-vocabulary LLM types fall back to decision."""
        import shutil

        from cairn.graph import embeddings as emb
        from cairn.llm.client import SubprocessBackend

        db_path, knowledge = self._store(tmp_path)

        def fake_extract(self, transcript, timeout=120):
            return [{"type": "Decision", "title": "captured note",
                     "body": "content", "confidence": 0.9}]

        monkeypatch.setattr(SubprocessBackend, "extract", fake_extract)
        monkeypatch.setenv("CAIRN_LLM_BACKEND", "droid")
        # The capture path skips a backend whose CLI is missing on PATH; the
        # hermetic suite default hides the real CLIs, so present one.
        monkeypatch.setattr(shutil, "which",
                            lambda name, *a, **k: "/fakebin/droid" if name == "droid" else None)

        def boom(conn, bundle, ids):
            raise RuntimeError("alias preflight failed")

        monkeypatch.setattr(emb, "embed_memory_concepts", boom)
        result = runner.invoke(_main(), [
            "memory", "capture", "--session-transcript", "[]",
            "--session-id", "s1", "--db", db_path, "--knowledge", knowledge,
        ])
        assert result.exit_code == 0, result.output
        assert "Captured 1 memories" in result.output

        bundle = OKFBundle(knowledge)
        cids = bundle.list_concepts(prefix="memory/")
        assert len(cids) == 1
        assert bundle.read_concept(cids[0]).extensions["memory_type"] == "decision"

        ro = get_db(db_path, read_only=True)
        try:
            rows = ro.execute("SELECT concept_id FROM memory_validity").fetchall()
        finally:
            ro.close()
        assert len(rows) == 1

    def test_batch_critic_keep_draft_preserves_id_and_commits(self, runner, tmp_path):
        """A KEEP_DRAFT pass keeps the concept id (refs/embeddings stay attached)
        and commits the validity projection instead of rolling it back at close."""
        db_path, knowledge = self._store(tmp_path)
        bundle = OKFBundle(knowledge)
        conn = get_db(db_path)
        try:
            cid = store_memory(
                create_memory(type_="pattern", title="critic draft", body="content", confidence=0.2),
                bundle, tier="drafts",
            )
            conn.commit()
        finally:
            conn.close()

        result = runner.invoke(_main(), [
            "memory", "batch-critic", "--db", db_path, "--knowledge", knowledge,
        ])
        assert result.exit_code == 0, result.output
        assert "1 remain drafts" in result.output

        assert bundle.list_concepts(prefix="memory/drafts") == [cid]

        ro = get_db(db_path, read_only=True)
        try:
            rows = [r["concept_id"] for r in
                    ro.execute("SELECT concept_id FROM memory_validity").fetchall()]
        finally:
            ro.close()
        assert rows == [cid]

    def test_list_with_corrupt_db_maps_to_cli_error(self, runner, tmp_path):
        """A non-DB file at --db yields a CLI error message, not a raw traceback."""
        knowledge = str(tmp_path / "k")
        bundle = OKFBundle(knowledge)
        store_memory(
            create_memory(type_="pattern", title="listable", body="content", confidence=0.7),
            bundle,
        )
        db_path = tmp_path / "junk.db"
        db_path.write_text("this is not a database")
        result = runner.invoke(_main(), [
            "memory", "list", "--db", str(db_path), "--knowledge", knowledge,
        ])
        assert result.exit_code == 1
        assert "not a valid cairn database" in result.output

    def test_digest_negative_limit_is_usage_error(self, runner, tmp_path):
        """A negative --limit maps to a usage error instead of a traceback."""
        result = runner.invoke(_main(), [
            "memory", "digest", "--limit", "-1",
            "--knowledge", str(tmp_path / "k"),
        ])
        assert result.exit_code == 2
        assert "non-negative" in result.output

    def test_list_invalid_tier_is_usage_error(self, runner, tmp_path):
        """An invalid --tier maps to a usage error instead of a KeyError traceback."""
        result = runner.invoke(_main(), [
            "memory", "list", "--tier", "bogus",
            "--knowledge", str(tmp_path / "k"),
        ])
        assert result.exit_code == 2
        assert "must be one of" in result.output

    def test_capture_missing_backend_queues_one_task_without_poll(
        self, runner, tmp_path, monkeypatch
    ):
        """A configured-but-missing backend queues ONE task and never enters the queue poll."""
        import shutil

        from cairn.llm import tasks as task_mod

        db_path, knowledge = self._store(tmp_path)
        monkeypatch.setenv("CAIRN_LLM_BACKEND", "droid")
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)

        polled = []

        def fake_run_task(self, kind, resource, facts, timeout=120):
            polled.append(kind)
            return ""

        monkeypatch.setattr(
            "cairn.llm.client.FileQueueBackend._run_task", fake_run_task
        )
        result = runner.invoke(_main(), [
            "memory", "capture", "--session-transcript", "[]",
            "--session-id", "s1", "--db", db_path, "--knowledge", knowledge,
        ])
        assert result.exit_code == 0, result.output
        assert polled == []
        assert "not found on PATH" in result.output
        tasks = task_mod.list_tasks(OKFBundle(knowledge), kind="memory-extract")
        assert len(tasks) == 1

    def test_capture_reports_unrecordable_candidates(self, runner, tmp_path, monkeypatch):
        """Malformed candidates are counted and reported, not silently dropped."""
        import shutil

        from cairn.llm.client import SubprocessBackend

        db_path, knowledge = self._store(tmp_path)
        monkeypatch.setenv("CAIRN_LLM_BACKEND", "droid")
        monkeypatch.setattr(
            shutil, "which",
            lambda name, *a, **k: "/fakebin/droid" if name == "droid" else None,
        )

        def fake_extract(self, transcript, timeout=120):
            return [
                {"type": "decision", "title": "good note", "body": "content",
                 "confidence": 0.9},
                {"type": "decision", "title": "bad note", "body": "content",
                 "confidence": "high"},
            ]

        monkeypatch.setattr(SubprocessBackend, "extract", fake_extract)
        result = runner.invoke(_main(), [
            "memory", "capture", "--session-transcript", "[]",
            "--session-id", "s1", "--db", db_path, "--knowledge", knowledge,
        ])
        assert result.exit_code == 0, result.output
        assert "Captured 1 memories" in result.output
        assert "Skipped 1 unrecordable candidate(s)" in result.output

    def test_record_lock_timeout_maps_to_cli_error(self, runner, tmp_path, monkeypatch):
        """A bundle-lock timeout surfaces as a CLI error message, not a traceback."""
        from cairn.okf.bundle import OKFBundle as _Bundle

        db_path, knowledge = self._store(tmp_path)

        def locked(self, timeout=5.0):
            raise TimeoutError("another process holds the lock")

        monkeypatch.setattr(_Bundle, "lock", locked)
        result = runner.invoke(_main(), [
            "memory", "record", "decision", "locked out",
            "--db", db_path, "--knowledge", knowledge,
        ])
        assert result.exit_code == 1
        assert "knowledge bundle is busy" in result.output


def _main():
    from cairn.cli.main import main

    return main
