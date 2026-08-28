import json
import shutil
import tempfile
from pathlib import Path

import pytest

from cairn.agent_install import check_installed, install, uninstall, CLIENTS


@pytest.fixture
def _cairn_bin_pinned(monkeypatch):
    """Generated configs resolve `cairn` to a fixed fake binary path (and no
    agent CLI is visible), so pinned config shapes are machine-independent."""
    monkeypatch.setattr(shutil, "which",
                        lambda cmd, path=None: "/fake/bin/cairn" if cmd == "cairn" else None)


def _custom_home_env(monkeypatch) -> dict[str, str]:
    """Point CAIRN_HOME at a non-default home (tilde form); returns the env
    block generated configs must carry -- the expanded absolute path."""
    monkeypatch.setenv("CAIRN_HOME", "~/custom-cairn-home")
    return {"CAIRN_HOME": str(Path.home() / "custom-cairn-home")}


def test_clients_list_includes_opencode():
    assert "opencode" in CLIENTS


def test_install_uninstall_opencode():
    """opencode reads `opencode.json` at the project root with a top-level
    `mcp` key (NOT mcpServers) and a per-server `command` array. This verifies
    the installer writes that exact schema and that uninstall reverses it,
    including cleaning up a legacy .opencode/mcp.json from older installers.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        (ws / ".opencode").mkdir(parents=True, exist_ok=True)

        report = install(str(ws), clients=["opencode"], force=True, transport="stdio")
        assert any(r.client == "opencode" for r in report.results)

        # opencode reads opencode.json at the project root -- NOT .opencode/mcp.json.
        config_path = ws / "opencode.json"
        assert config_path.exists(), "install must write opencode.json at the project root"

        data = json.loads(config_path.read_text(encoding="utf-8"))

        # Schema: top-level "mcp" key (NOT "mcpServers").
        assert "mcp" in data, "opencode config must use the `mcp` key, not `mcpServers`"
        assert "mcpServers" not in data, "opencode does not read `mcpServers`"

        server = data["mcp"]["cairn"]
        # stdio => type "local" + command as a single array including "serve".
        assert server["type"] == "local", f"stdio server must be type 'local', got {server['type']!r}"
        assert server["enabled"] is True
        assert isinstance(server["command"], list), "opencode command must be an array"
        assert server["command"][-1] == "serve", "command array must end with 'serve'"

        # Uninstall reverses it.
        un_report = uninstall(str(ws), clients=["opencode"])
        assert any(r.client == "opencode" for r in un_report.results)
        after = json.loads(config_path.read_text(encoding="utf-8"))
        assert "cairn" not in after.get("mcp", {}), "uninstall must remove the cairn server"


def test_install_opencode_preserves_other_servers():
    """Deep-merge must keep other servers in opencode.json untouched."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        config_path = ws / "opencode.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        # Pre-existing opencode.json with another server + an unrelated top-level key.
        config_path.write_text(json.dumps({
            "mcp": {"other-server": {"type": "local", "command": ["foo"]}},
            "theme": "dark",
        }), encoding="utf-8")

        install(str(ws), clients=["opencode"], force=True, transport="stdio")

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "cairn" in data["mcp"], "cairn server must be added"
        assert "other-server" in data["mcp"], "pre-existing server must be preserved"
        assert data["theme"] == "dark", "unrelated top-level keys must be preserved"


def test_install_opencode_sse_uses_remote_type():
    """SSE transport must emit type 'remote' with a url, per opencode's schema."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        install(str(ws), clients=["opencode"], force=True, transport="sse")
        data = json.loads((ws / "opencode.json").read_text(encoding="utf-8"))
        server = data["mcp"]["cairn"]
        assert server["type"] == "remote", f"sse server must be type 'remote', got {server['type']!r}"
        assert "url" in server and server["url"].startswith("http")


def test_uninstall_opencode_removes_legacy_mcp_json():
    """An older installer wrote .opencode/mcp.json (a path opencode never read).
    Uninstall must clean that up too, so users aren't left with a stale file.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        legacy = ws / ".opencode" / "mcp.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"mcpServers": {"cairn": {"command": "cg"}}}), encoding="utf-8")

        uninstall(str(ws), clients=["opencode"])

        assert not legacy.exists(), "legacy .opencode/mcp.json must be removed on uninstall"


def test_install_opencode_global_scope_writes_global_path(tmp_path, monkeypatch):
    """--scope global must land in ~/.config/opencode/opencode.json (the path
    check_installed probes), NOT the workspace root -- and be detected."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda *a, **k: fake_home)
    ws = tmp_path / "ws"
    ws.mkdir()

    install(str(ws), clients=["opencode"], force=True, transport="stdio", scope="global")

    global_cfg = fake_home / ".config" / "opencode" / "opencode.json"
    assert global_cfg.exists(), "scope=global must write the global opencode.json"
    assert not (ws / "opencode.json").exists(), "scope=global must not write the workspace root"
    assert check_installed(str(ws))["opencode"], "global install must be detected"


def test_uninstall_opencode_global_scope_strips_global_path(tmp_path, monkeypatch):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda *a, **k: fake_home)
    ws = tmp_path / "ws"
    ws.mkdir()

    install(str(ws), clients=["opencode"], force=True, transport="stdio", scope="global")
    uninstall(str(ws), clients=["opencode"], scope="global")

    global_cfg = fake_home / ".config" / "opencode" / "opencode.json"
    after = json.loads(global_cfg.read_text(encoding="utf-8"))
    assert "cairn" not in after.get("mcp", {}), "scope=global uninstall must strip the global file"


# --------------------------------------------------------------------------
# FR-001: command-array shapes (opencode + kilo) embed env.CAIRN_HOME iff the
# home is non-default; default home stays byte-identical to the env-less shape
# --------------------------------------------------------------------------

def test_custom_home_command_array_generators_embed_env(_cairn_bin_pinned, monkeypatch):
    from cairn.agent_install.clients.kilo import kilo_mcp_config_json
    from cairn.agent_install.clients.opencode import opencode_mcp_config_json

    env = _custom_home_env(monkeypatch)
    expected_entry = {"type": "local", "command": ["/fake/bin/cairn", "serve"],
                      "enabled": True, "env": env}
    assert opencode_mcp_config_json(transport="stdio") == {"mcp": {"cairn": expected_entry}}
    assert kilo_mcp_config_json(transport="stdio") == {"mcp": {"cairn": expected_entry}}


def test_custom_home_command_array_install_writes_env(_cairn_bin_pinned, tmp_path, monkeypatch):
    env = _custom_home_env(monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()

    install(str(ws), clients=["opencode", "kilo"], force=True, transport="stdio")

    opencode = json.loads((ws / "opencode.json").read_text(encoding="utf-8"))
    kilo = json.loads((ws / "kilo.json").read_text(encoding="utf-8"))
    assert opencode["mcp"]["cairn"]["env"] == env
    assert kilo["mcp"]["cairn"]["env"] == env


def test_default_home_command_array_generators_stay_env_less(_cairn_bin_pinned, monkeypatch):
    from cairn.agent_install.clients.kilo import kilo_mcp_config_json
    from cairn.agent_install.clients.opencode import opencode_mcp_config_json

    monkeypatch.delenv("CAIRN_HOME")
    expected_entry = {"type": "local", "command": ["/fake/bin/cairn", "serve"],
                      "enabled": True}
    assert opencode_mcp_config_json(transport="stdio") == {"mcp": {"cairn": expected_entry}}
    assert kilo_mcp_config_json(transport="stdio") == {"mcp": {"cairn": expected_entry}}


def test_home_set_to_default_command_array_generators_match_unset(_cairn_bin_pinned, monkeypatch):
    from cairn.agent_install.clients.kilo import kilo_mcp_config_json
    from cairn.agent_install.clients.opencode import opencode_mcp_config_json

    generators = (opencode_mcp_config_json, kilo_mcp_config_json)
    monkeypatch.delenv("CAIRN_HOME")
    unset = [json.dumps(gen(transport="stdio"), sort_keys=True) for gen in generators]
    monkeypatch.setenv("CAIRN_HOME", str(Path.home() / ".cairn"))
    defaulted = [json.dumps(gen(transport="stdio"), sort_keys=True) for gen in generators]
    assert defaulted == unset


def test_default_home_command_array_install_writes_no_env(_cairn_bin_pinned, tmp_path, monkeypatch):
    monkeypatch.delenv("CAIRN_HOME")
    ws = tmp_path / "ws"
    ws.mkdir()

    install(str(ws), clients=["opencode", "kilo"], force=True, transport="stdio")

    opencode = json.loads((ws / "opencode.json").read_text(encoding="utf-8"))
    kilo = json.loads((ws / "kilo.json").read_text(encoding="utf-8"))
    assert "env" not in opencode["mcp"]["cairn"]
    assert "env" not in kilo["mcp"]["cairn"]


def test_home_set_to_default_command_array_files_byte_identical_to_unset(
        _cairn_bin_pinned, tmp_path, monkeypatch):
    monkeypatch.delenv("CAIRN_HOME")
    ws_unset = tmp_path / "ws_unset"
    ws_unset.mkdir()
    install(str(ws_unset), clients=["opencode", "kilo"], force=True, transport="stdio")
    unset = ((ws_unset / "opencode.json").read_bytes(),
             (ws_unset / "kilo.json").read_bytes())

    monkeypatch.setenv("CAIRN_HOME", str(Path.home() / ".cairn"))
    ws_default = tmp_path / "ws_default"
    ws_default.mkdir()
    install(str(ws_default), clients=["opencode", "kilo"], force=True, transport="stdio")

    assert ((ws_default / "opencode.json").read_bytes(),
            (ws_default / "kilo.json").read_bytes()) == unset
