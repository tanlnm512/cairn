"""Installer config-shape pins: URL+key registrations for the 8 supported
clients and the claude-desktop unsupported report."""

import importlib
import json
import re
from pathlib import Path

import pytest

from cairn.agent_install import CLIENTS, check_installed, install, uninstall

# Independent truth (FR-005 default port, D-007 single /mcp path).
DAEMON_URL = "http://127.0.0.1:9876/mcp"

URL_CLIENTS = ["claude", "cursor", "droid", "zcode", "agy", "opencode", "kilo", "omp"]

_TEST_KEY = "test-key-k1-0123456789abcdef"

# Every module the generators may bind the key reader through; patching them
# all keeps the pin independent of the generators' import style.
_KEY_MODULE_NAMES = [
    "cairn.mcp_server.auth",
    "cairn.agent_install._common",
    *[
        f"cairn.agent_install.clients.{n}"
        for n in (
            "claude",
            "claude_desktop",
            "cursor",
            "droid",
            "zcode",
            "agy",
            "opencode",
            "kilo",
            "omp",
        )
    ],
]


@pytest.fixture
def pinned_api_key(monkeypatch):
    """Pin the persisted-key reader so the embedded credential in every
    generated config is exactly this key."""
    for name in _KEY_MODULE_NAMES:
        monkeypatch.setattr(
            importlib.import_module(name),
            "persisted_api_key",
            lambda: _TEST_KEY,
            raising=False,
        )
    return _TEST_KEY


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_client_roster_is_eight_url_clients_plus_desktop():
    assert set(CLIENTS) == set(URL_CLIENTS) | {"claude-desktop"}


# --------------------------------------------------------------------------
# Generator shapes (the single shape authority for FR-006)
# --------------------------------------------------------------------------


def test_shared_mcp_servers_generator_is_url_plus_key(pinned_api_key):
    from cairn.agent_install._common import mcp_config_json

    assert mcp_config_json() == {
        "mcpServers": {
            "cairn": {
                "type": "http",
                "url": DAEMON_URL,
                "headers": _bearer(pinned_api_key),
            }
        }
    }


def test_zcode_generator_is_url_plus_key(pinned_api_key):
    from cairn.agent_install.clients.zcode import zcode_mcp_config_json

    assert zcode_mcp_config_json() == {
        "mcp": {
            "servers": {
                "cairn": {
                    "type": "http",
                    "url": DAEMON_URL,
                    "headers": _bearer(pinned_api_key),
                }
            }
        }
    }


def test_agy_generator_is_url_plus_key(pinned_api_key):
    from cairn.agent_install.clients.agy import agy_mcp_config_json

    assert agy_mcp_config_json() == {
        "mcpServers": {
            "cairn": {
                "type": "http",
                "serverUrl": DAEMON_URL,
                "headers": _bearer(pinned_api_key),
            }
        }
    }


def test_opencode_and_kilo_generators_are_url_plus_key(pinned_api_key):
    from cairn.agent_install.clients.kilo import kilo_mcp_config_json
    from cairn.agent_install.clients.opencode import opencode_mcp_config_json

    expected = {
        "mcp": {
            "cairn": {
                "type": "remote",
                "url": DAEMON_URL,
                "enabled": True,
                "headers": _bearer(pinned_api_key),
            }
        }
    }
    assert opencode_mcp_config_json() == expected
    assert kilo_mcp_config_json() == expected


# --------------------------------------------------------------------------
# Install writes URL+key for every supported client (TC-014 pytest pin)
# --------------------------------------------------------------------------


def test_install_all_writes_url_key_for_every_supported_client(
    tmp_path, pinned_api_key
):
    ws = tmp_path / "ws"
    ws.mkdir()

    install(str(ws), clients=["all"])

    shared = {"type": "http", "url": DAEMON_URL, "headers": _bearer(pinned_api_key)}
    for client, path in {
        "claude": ws / ".mcp.json",
        "cursor": ws / ".cursor" / "mcp.json",
        "droid": ws / ".factory" / "mcp.json",
        "omp": ws / ".omp" / "mcp.json",
    }.items():
        assert path.exists(), f"{client}: install must write {path}"
        entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["cairn"]
        assert entry == shared, f"{client}: URL+key entry expected"

    zc = json.loads((ws / ".zcode" / "config.json").read_text(encoding="utf-8"))
    assert zc["mcp"]["servers"]["cairn"] == shared

    remote = {
        "type": "remote",
        "url": DAEMON_URL,
        "enabled": True,
        "headers": _bearer(pinned_api_key),
    }
    for client, path in (
        ("opencode", ws / "opencode.json"),
        ("kilo", ws / "kilo.json"),
    ):
        entry = json.loads(path.read_text(encoding="utf-8"))["mcp"]["cairn"]
        assert entry == remote, f"{client}: remote URL+key entry expected"

    from cairn.agent_install.clients.agy import agy_config_path

    agy = json.loads(agy_config_path().read_text(encoding="utf-8"))
    assert agy["mcpServers"]["cairn"] == {
        "type": "http",
        "serverUrl": DAEMON_URL,
        "headers": _bearer(pinned_api_key),
    }


# --------------------------------------------------------------------------
# Claude Desktop: reported unsupported, nothing written (TC-015 pytest pin)
# --------------------------------------------------------------------------


def test_claude_desktop_is_reported_unsupported_and_writes_no_config(
    tmp_path, pinned_api_key
):
    from cairn.agent_install.detect import claude_desktop_config_path

    ws = tmp_path / "ws"
    ws.mkdir()
    rep = install(str(ws), clients=["claude-desktop"])

    res = next(r for r in rep.results if r.client == "claude-desktop")
    assert res.written == [], "an unsupported client must have nothing written"
    assert not claude_desktop_config_path().exists(), (
        "no Claude Desktop config file may be written"
    )
    assert any(
        re.search(r"unsupport|not supported|skip", n, re.I) for n in res.notes
    ), res.notes


def test_claude_desktop_uninstall_still_strips_legacy_entries(tmp_path):
    from cairn.agent_install.detect import claude_desktop_config_path

    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = claude_desktop_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({"mcpServers": {"cairn": {"command": "cairn", "args": ["serve"]}}}),
        encoding="utf-8",
    )

    uninstall(str(ws), clients=["claude-desktop"])

    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert "cairn" not in after.get("mcpServers", {})


# --------------------------------------------------------------------------
# opencode lifecycle on the URL+key shape
# --------------------------------------------------------------------------


def test_install_uninstall_opencode(tmp_path, pinned_api_key):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".opencode").mkdir(parents=True, exist_ok=True)

    report = install(str(ws), clients=["opencode"], force=True)
    assert any(r.client == "opencode" for r in report.results)

    config_path = ws / "opencode.json"
    assert config_path.exists(), "install must write opencode.json at the project root"

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "mcp" in data, "opencode config must use the `mcp` key, not `mcpServers`"
    assert "mcpServers" not in data, "opencode does not read `mcpServers`"
    server = data["mcp"]["cairn"]
    assert server["type"] == "remote"
    assert server["url"] == DAEMON_URL
    assert server["headers"] == _bearer(pinned_api_key)

    un_report = uninstall(str(ws), clients=["opencode"])
    assert any(r.client == "opencode" for r in un_report.results)
    after = json.loads(config_path.read_text(encoding="utf-8"))
    assert "cairn" not in after.get("mcp", {}), "uninstall must remove the cairn server"


def test_install_opencode_preserves_other_servers(tmp_path, pinned_api_key):
    ws = tmp_path / "ws"
    ws.mkdir()
    config_path = ws / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "mcp": {"other-server": {"type": "local", "command": ["foo"]}},
                "theme": "dark",
            }
        ),
        encoding="utf-8",
    )

    install(str(ws), clients=["opencode"], force=True)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "cairn" in data["mcp"], "cairn server must be added"
    assert "other-server" in data["mcp"], "pre-existing server must be preserved"
    assert data["theme"] == "dark", "unrelated top-level keys must be preserved"


def test_uninstall_opencode_removes_legacy_mcp_json(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    legacy = ws / ".opencode" / "mcp.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps({"mcpServers": {"cairn": {"command": "cg"}}}), encoding="utf-8"
    )

    uninstall(str(ws), clients=["opencode"])

    assert not legacy.exists(), "legacy .opencode/mcp.json must be removed on uninstall"


def test_install_opencode_global_scope_writes_global_path(
    tmp_path, monkeypatch, pinned_api_key
):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda *a, **k: fake_home)
    ws = tmp_path / "ws"
    ws.mkdir()

    install(str(ws), clients=["opencode"], force=True, scope="global")

    global_cfg = fake_home / ".config" / "opencode" / "opencode.json"
    assert global_cfg.exists(), "scope=global must write the global opencode.json"
    assert not (ws / "opencode.json").exists(), (
        "scope=global must not write the workspace root"
    )
    assert check_installed(str(ws))["opencode"], "global install must be detected"


def test_uninstall_opencode_global_scope_strips_global_path(
    tmp_path, monkeypatch, pinned_api_key
):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda *a, **k: fake_home)
    ws = tmp_path / "ws"
    ws.mkdir()

    install(str(ws), clients=["opencode"], force=True, scope="global")
    uninstall(str(ws), clients=["opencode"], scope="global")

    global_cfg = fake_home / ".config" / "opencode" / "opencode.json"
    after = json.loads(global_cfg.read_text(encoding="utf-8"))
    assert "cairn" not in after.get("mcp", {}), (
        "scope=global uninstall must strip the global file"
    )


# --------------------------------------------------------------------------
# Re-heal, stray-file preservation, and truthful reporting
# --------------------------------------------------------------------------


def test_uninstall_rejects_unknown_client_names(tmp_path):
    """API symmetry with install(): a mistyped client name raises, not no-op."""
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(ValueError, match="Unknown clients"):
        uninstall(str(ws), clients=["cluade"])


def test_uninstall_opencode_strips_legacy_mcp_json_preserving_other_servers(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    legacy = ws / ".opencode" / "mcp.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cairn": {"command": "cg"},
                    "user-server": {"command": "foo"},
                }
            }
        ),
        encoding="utf-8",
    )

    uninstall(str(ws), clients=["opencode"])

    data = json.loads(legacy.read_text(encoding="utf-8"))
    assert "cairn" not in data["mcpServers"]
    assert "user-server" in data["mcpServers"], (
        "unrelated servers in the legacy file must survive uninstall"
    )


def test_reinstall_rewrites_disabled_or_stale_key_remote(tmp_path, pinned_api_key):
    """A user-disabled entry or rotated auth header must read as not-installed
    so the reinstall restores cairn's intended registration."""
    ws = tmp_path / "ws"
    ws.mkdir()
    install(str(ws), clients=["opencode"])
    cfg = ws / "opencode.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["mcp"]["cairn"]["enabled"] = False
    data["mcp"]["cairn"]["headers"] = _bearer("rotated-old-key")
    cfg.write_text(json.dumps(data), encoding="utf-8")

    install(str(ws), clients=["opencode"])

    healed = json.loads(cfg.read_text(encoding="utf-8"))["mcp"]["cairn"]
    assert healed["enabled"] is True
    assert healed["headers"] == _bearer(pinned_api_key)


def test_opencode_reinstall_note_reports_no_change(tmp_path, pinned_api_key):
    """An already-installed registration must note 'no change', not claim
    'MCP server written to ...'."""
    ws = tmp_path / "ws"
    ws.mkdir()
    install(str(ws), clients=["opencode"])

    res = next(
        r for r in install(str(ws), clients=["opencode"]).results
        if r.client == "opencode"
    )

    assert not any(n.startswith("MCP server written") for n in res.notes)
    assert any("already registered" in n for n in res.notes)


def test_agy_workspace_scope_notes_global_only_surface(
    tmp_path, monkeypatch, pinned_api_key
):
    """agy reads only a global MCP config; a workspace-scope install must say
    so instead of silently writing the global path."""
    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda *a, **k: home)
    ws = tmp_path / "ws"
    ws.mkdir()

    res = next(
        r for r in install(str(ws), clients=["agy"], scope="workspace").results
        if r.client == "agy"
    )

    assert any("workspace scope" in n for n in res.notes)
