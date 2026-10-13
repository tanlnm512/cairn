"""Unit tests for the bearer-key verifier module (resolve_api_key, bearer_key_ok)."""
from __future__ import annotations

import hmac

import pytest

_KEY = "unit-test-secret"


@pytest.fixture(scope="module")
def auth():
    import cairn.mcp_server.auth as auth_mod

    return auth_mod


def test_flag_beats_env(auth, monkeypatch):
    monkeypatch.setenv("CAIRN_MCP_API_KEY", "env-key")
    assert auth.resolve_api_key("flag-key") == "flag-key"


def test_env_used_without_flag(auth, monkeypatch):
    monkeypatch.setenv("CAIRN_MCP_API_KEY", "env-key")
    assert auth.resolve_api_key(None) == "env-key"


def test_no_sources_returns_none(auth, monkeypatch):
    monkeypatch.delenv("CAIRN_MCP_API_KEY", raising=False)
    assert auth.resolve_api_key(None) is None


def test_empty_flag_falls_through(auth, monkeypatch):
    monkeypatch.setenv("CAIRN_MCP_API_KEY", "env-key")
    assert auth.resolve_api_key("") == "env-key"
    monkeypatch.delenv("CAIRN_MCP_API_KEY")
    assert auth.resolve_api_key("") is None


def test_persisted_api_key_absent_file_returns_none(auth):
    assert auth.persisted_api_key() is None


def test_persisted_api_key_present_file_returns_value(auth):
    from cairn import paths

    paths.CAIRN_HOME.mkdir(parents=True, exist_ok=True)
    (paths.CAIRN_HOME / "mcp-api-key").write_text("persisted-secret")
    assert auth.persisted_api_key() == "persisted-secret"


def test_resolve_api_key_ignores_persisted_key_file(auth, monkeypatch):
    from cairn import paths

    paths.CAIRN_HOME.mkdir(parents=True, exist_ok=True)
    (paths.CAIRN_HOME / "mcp-api-key").write_text("persisted-secret")
    assert auth.persisted_api_key() == "persisted-secret"
    monkeypatch.delenv("CAIRN_MCP_API_KEY", raising=False)
    assert auth.resolve_api_key(None) is None


def test_persisted_api_key_follows_env_repointed_after_import(auth, monkeypatch, tmp_path):
    """The key file must follow a CAIRN_HOME env change made after import
    (call-time resolution), not the stale import-time paths.CAIRN_HOME binding."""
    from cairn import paths

    env_home = tmp_path / "env_home"
    env_home.mkdir()
    (env_home / "mcp-api-key").write_text("env-home-secret")
    monkeypatch.setattr(paths, "CAIRN_HOME", tmp_path / "stale_import_binding")
    monkeypatch.setenv("CAIRN_HOME", str(env_home))
    assert auth.persisted_api_key() == "env-home-secret"


def test_raw_header_correct_key(auth):
    assert auth.bearer_key_ok(f"Bearer {_KEY}", _KEY) is True


def test_bare_token_correct_key(auth):
    assert auth.bearer_key_ok(_KEY, _KEY) is True


def test_wrong_key_rejected(auth):
    assert auth.bearer_key_ok(f"Bearer not-{_KEY}", _KEY) is False
    assert auth.bearer_key_ok(f"not-{_KEY}", _KEY) is False


def test_same_length_wrong_key_rejected(auth):
    wrong = "x" * len(_KEY)
    assert auth.bearer_key_ok(f"Bearer {wrong}", _KEY) is False
    assert auth.bearer_key_ok(wrong, _KEY) is False


@pytest.mark.parametrize(
    "header",
    [None, "", "Basic dXNlcjpwYXNz", "Bearer", "Bearer   ", "Bearer a b"],
)
def test_missing_or_malformed_header_rejected(auth, header):
    assert auth.bearer_key_ok(header, _KEY) is False


def test_non_ascii_input_rejected_without_raising(auth):
    assert auth.bearer_key_ok("Bearer clé-✓-non-ascii", _KEY) is False


def test_comparison_is_constant_time_compare(auth, monkeypatch):
    seen = []

    def spy(a, b):
        seen.append((a, b))
        return hmac.compare_digest(a, b)

    monkeypatch.setattr(auth, "compare_digest", spy)
    assert auth.bearer_key_ok(f"Bearer {_KEY}", _KEY) is True
    assert seen == [(_KEY.encode("utf-8"), _KEY.encode("utf-8"))]
