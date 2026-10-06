from __future__ import annotations

import json
import os
import time
from importlib import import_module
from pathlib import Path

import pytest


def _patch_registration(
    monkeypatch, path: Path, client: str, workspace_owned: bool
):
    from cairn.agent_install import _registration_entry
    doctor = import_module("cairn.cli.system.doctor")

    def enumerate_registrations():
        entry = _registration_entry(str(path))
        return [] if entry is None else [
            (client, path, f"config/{path.name}", entry, workspace_owned)
        ]

    monkeypatch.setattr(doctor, "_enumerate_registrations", enumerate_registrations)


def _dead_endpoint(monkeypatch):
    from cairn.mcp_server import lifecycle

    monkeypatch.setattr(lifecycle, "sse_responds", lambda **_: False)


def _age(path: Path) -> None:
    stamp = time.time() - 3600
    os.utime(path, (stamp, stamp))


@pytest.mark.parametrize(
    ("client", "initial"),
    [
        ("claude", {"mcpServers": {}}),
        ("cursor", {"mcpServers": {}}),
        ("droid", {"mcpServers": {}}),
        ("omp", {"mcpServers": {}}),
        ("agy", {"mcpServers": {}}),
        ("claude-desktop", {"mcpServers": {}}),
        ("zcode", {"mcp": {"servers": {}}}),
        ("opencode", {"mcp": {}}),
        ("kilo", {"mcp": {}}),
    ],
)
def test_repoints_each_client_shape(tmp_path, monkeypatch, client, initial):
    from cairn.agent_install import (
        kilo_mcp_config_json,
        mcp_config_json,
        mcp_config_json_desktop,
        opencode_mcp_config_json,
        zcode_mcp_config_json,
    )
    from cairn.agent_install.clients.agy import agy_mcp_config_json
    from cairn.cli.system.doctor_fix import apply_doctor_fixes

    url = "http://127.0.0.1:65535/sse"
    entry = {"serverUrl": url} if client == "agy" else {"url": url}
    foreign = {"command": "/bin/other", "args": ["serve"]}
    if client == "zcode":
        initial["mcp"]["servers"]["other"] = foreign
        initial["mcp"]["servers"]["cairn"] = entry
        expected = zcode_mcp_config_json()
        expected["mcp"]["servers"]["other"] = foreign
    elif client in {"opencode", "kilo"}:
        initial["mcp"]["other"] = foreign
        initial["mcp"]["cairn"] = {
            "type": "remote", "url": url, "enabled": True
        }
        generated = (
            opencode_mcp_config_json()
            if client == "opencode"
            else kilo_mcp_config_json()
        )
        expected = {"mcp": {"cairn": generated["mcp"]["cairn"]}}
        expected["mcp"]["other"] = foreign
    else:
        initial["mcpServers"]["other"] = foreign
        initial["mcpServers"]["cairn"] = entry
        if client == "claude-desktop":
            pinned_ws = str(tmp_path / "pinned-workspace")
            entry["env"] = {"CAIRN_WORKSPACE": pinned_ws}
            generated = mcp_config_json_desktop(pinned_ws)
        elif client == "agy":
            generated = agy_mcp_config_json()
        else:
            generated = mcp_config_json()
        expected = {"mcpServers": {"cairn": generated["mcpServers"]["cairn"]}}
        expected["mcpServers"]["other"] = foreign

    initial["unrelated"] = {"token": "secret-value"}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(initial), encoding="utf-8")
    before = path.read_bytes()
    _age(path)
    _patch_registration(monkeypatch, path, client, workspace_owned=False)
    _dead_endpoint(monkeypatch)

    actions = apply_doctor_fixes(str(tmp_path / "store.db"))

    assert actions == [{
        "client": client,
        "config": "config/config.json",
        "action": "repointed-to-stdio",
        "url": url,
    }]
    assert "secret-value" not in json.dumps(actions)
    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert rewritten.pop("unrelated") == {"token": "secret-value"}
    assert rewritten == expected
    assert path.with_suffix(".json.bak").read_bytes() == before
    assert apply_doctor_fixes(str(tmp_path / "store.db")) == []


def test_healthy_endpoint_returns_no_actions(tmp_path, monkeypatch):
    from cairn.cli.system.doctor_fix import apply_doctor_fixes
    from cairn.mcp_server import lifecycle

    path = tmp_path / "config.json"
    initial = {"mcpServers": {"cairn": {"url": "http://127.0.0.1:1/sse"}}}
    path.write_text(json.dumps(initial), encoding="utf-8")
    _patch_registration(monkeypatch, path, "claude", workspace_owned=False)
    monkeypatch.setattr(lifecycle, "sse_responds", lambda **_: True)

    assert apply_doctor_fixes(str(tmp_path / "store.db")) == []
    assert json.loads(path.read_text(encoding="utf-8")) == initial
    assert not path.with_suffix(".json.bak").exists()


def test_stdio_registration_with_stale_url_is_unchanged(tmp_path, monkeypatch):
    from cairn.cli.system.doctor_fix import apply_doctor_fixes

    path = tmp_path / "config.json"
    initial = {
        "mcpServers": {
            "cairn": {
                "command": "/bin/cairn",
                "args": ["serve"],
                "url": "http://127.0.0.1:1/sse",
            }
        }
    }
    path.write_text(json.dumps(initial), encoding="utf-8")
    _age(path)
    _patch_registration(monkeypatch, path, "claude", workspace_owned=False)
    _dead_endpoint(monkeypatch)

    assert apply_doctor_fixes(str(tmp_path / "store.db")) == []
    assert json.loads(path.read_text(encoding="utf-8")) == initial
    assert not path.with_suffix(".json.bak").exists()


def test_fresh_config_is_refused_without_backup(tmp_path, monkeypatch):
    from cairn.cli.system.doctor_fix import apply_doctor_fixes

    path = tmp_path / "config.json"
    initial = {"mcpServers": {"cairn": {"url": "http://127.0.0.1:1/sse"}}}
    path.write_text(json.dumps(initial), encoding="utf-8")
    _patch_registration(monkeypatch, path, "claude", workspace_owned=False)
    _dead_endpoint(monkeypatch)

    actions = apply_doctor_fixes(str(tmp_path / "store.db"))

    assert actions == [{
        "client": "claude",
        "config": "config/config.json",
        "action": "refused-recent-write",
        "url": "http://127.0.0.1:1/sse",
    }]
    assert json.loads(path.read_text(encoding="utf-8")) == initial
    assert not path.with_suffix(".json.bak").exists()


def test_workspace_owned_config_is_skipped_and_guided(tmp_path, monkeypatch):
    from cairn.cli.system.doctor_fix import apply_doctor_fixes

    path = tmp_path / ".mcp.json"
    initial = {"mcpServers": {"cairn": {"url": "http://127.0.0.1:1/sse"}}}
    path.write_text(json.dumps(initial), encoding="utf-8")
    _age(path)
    _patch_registration(monkeypatch, path, "claude", workspace_owned=True)
    _dead_endpoint(monkeypatch)

    actions = apply_doctor_fixes(str(tmp_path / "store.db"))

    assert actions == [{
        "client": "claude",
        "config": "config/.mcp.json",
        "action": "skipped-workspace-owned",
        "url": "http://127.0.0.1:1/sse",
    }]
    assert json.loads(path.read_text(encoding="utf-8")) == initial
    assert not path.with_suffix(".json.bak").exists()
