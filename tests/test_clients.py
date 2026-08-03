import json
import tempfile
from pathlib import Path

from cairn.agent_install import install, uninstall, CLIENTS


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
