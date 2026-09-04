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

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from cairn.graph.schema import _apply_schema
from cairn.memory.privacy import strip_private_data
from cairn.memory.promotion import (
    capture_memory,
    evolve_memory,
    search_memory,
)
from cairn.memory.scoring import (
    WEIGHTS,
    _freshness,
    _reinforcement,
    compute_score,
    score_memory,
)
from cairn.memory.store import tier_for_score
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

    def test_chain_ids_are_relative(self, db, bundle):
        """Supersession chain stores bundle-relative concept_ids."""
        capture_memory(db, bundle, type_="decision", title="chain test", body="v1", confidence=0.8)
        r2 = capture_memory(db, bundle, type_="decision", title="chain test", body="v2", confidence=0.85)
        new = bundle.read_concept(r2["path"])
        for cid in new.extensions["memory_supersedes"]:
            assert not os.path.isabs(cid), f"chain id {cid} must be relative"


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

    def test_dropped_signals_not_weighted(self):
        assert "critic_score" not in WEIGHTS
        assert "authority" not in WEIGHTS

    def test_reinforcement_weight_exists(self):
        assert "reinforcement" in WEIGHTS
        assert WEIGHTS["reinforcement"] > 0

    def test_freshness_weight_reduced(self):
        assert WEIGHTS["freshness"] == 0.0715
        assert WEIGHTS["reinforcement"] == 0.0715

    def test_weight_values_pinned(self):
        assert WEIGHTS == {
            "graph_verification": 0.357,
            "cross_session_refs": 0.286,
            "agent_confidence": 0.214,
            "freshness": 0.0715,
            "reinforcement": 0.0715,
        }


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

