from __future__ import annotations

import copy
import json
import os
import time
from importlib import import_module
from pathlib import Path

import pytest

DAEMON_URL = "http://127.0.0.1:9876/mcp"
_FOREIGN = {"command": "/bin/other", "args": ["serve"]}
# Seeds are literal pre-HTTP-era registration shapes; generating them would
# drift with the installer flip and silently untest the repoint.
_STDIO_SEED = {"command": "cairn", "args": ["serve"]}
_SSE_SEED = {"type": "sse", "url": "http://127.0.0.1:1/sse"}


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


def _age(path: Path) -> None:
    stamp = time.time() - 3600
    os.utime(path, (stamp, stamp))


def _cairn_parent(data: dict, client: str) -> dict:
    if client == "zcode":
        return data["mcp"]["servers"]
    if client in {"opencode", "kilo"}:
        return data["mcp"]
    return data["mcpServers"]


def _generated_entry(client: str) -> dict:
    from cairn.agent_install import (
        kilo_mcp_config_json,
        mcp_config_json,
        opencode_mcp_config_json,
        zcode_mcp_config_json,
    )
    from cairn.agent_install.clients.agy import agy_mcp_config_json

    generated = {
        "claude": mcp_config_json,
        "cursor": mcp_config_json,
        "droid": mcp_config_json,
        "omp": mcp_config_json,
        "agy": agy_mcp_config_json,
        "zcode": zcode_mcp_config_json,
        "opencode": opencode_mcp_config_json,
        "kilo": kilo_mcp_config_json,
    }[client]()
    return _cairn_parent(generated, client)["cairn"]


def _initial_config(client: str) -> dict:
    if client == "zcode":
        seed = {"type": "stdio", **copy.deepcopy(_STDIO_SEED)}
        return {"mcp": {"servers": {"other": copy.deepcopy(_FOREIGN), "cairn": seed}}}
    if client in {"opencode", "kilo"}:
        seed = {"type": "local", "command": ["cairn", "serve"], "enabled": True}
        return {"mcp": {"other": copy.deepcopy(_FOREIGN), "cairn": seed}}
    return {"mcpServers": {
        "other": copy.deepcopy(_FOREIGN),
        "cairn": copy.deepcopy(_STDIO_SEED),
    }}


def _assert_repointed_to_url(tmp_path, monkeypatch, client: str, initial: dict):
    from cairn.cli.system.doctor_fix import apply_doctor_fixes

    path = tmp_path / "config.json"
    path.write_text(json.dumps(initial), encoding="utf-8")
    before = path.read_bytes()
    _age(path)
    _patch_registration(monkeypatch, path, client, workspace_owned=False)

    actions = apply_doctor_fixes(str(tmp_path / "store.db"))

    assert actions == [{
        "client": client,
        "config": "config/config.json",
        "action": "repointed-to-url",
        "url": DAEMON_URL,
    }]
    assert "secret-value" not in json.dumps(actions)
    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert rewritten.pop("unrelated") == {"token": "secret-value"}
    expected = copy.deepcopy(initial)
    del expected["unrelated"]
    _cairn_parent(expected, client)["cairn"] = _generated_entry(client)
    assert rewritten == expected
    entry = _cairn_parent(rewritten, client)["cairn"]
    assert "command" not in entry
    assert "127.0.0.1:9876" in json.dumps(entry)
    assert path.with_suffix(".json.bak").read_bytes() == before
    assert apply_doctor_fixes(str(tmp_path / "store.db")) == []


@pytest.mark.parametrize(
    "client",
    [
        "claude",
        "cursor",
        "droid",
        "omp",
        "agy",
        "zcode",
        "opencode",
        "kilo",
    ],
)
def test_repoints_stdio_registrations_to_url(tmp_path, monkeypatch, client):
    initial = _initial_config(client)
    initial["unrelated"] = {"token": "secret-value"}
    _assert_repointed_to_url(tmp_path, monkeypatch, client, initial)


def test_repoints_sse_registration_to_url(tmp_path, monkeypatch):
    initial = {"mcpServers": {
        "other": copy.deepcopy(_FOREIGN),
        "cairn": copy.deepcopy(_SSE_SEED),
    }}
    initial["unrelated"] = {"token": "secret-value"}
    _assert_repointed_to_url(tmp_path, monkeypatch, "claude", initial)


def test_desktop_registration_gets_removal_hint_and_is_never_repointed(
    tmp_path, monkeypatch
):
    from cairn.cli.system.doctor_fix import apply_doctor_fixes

    path = tmp_path / "config.json"
    initial = {"mcpServers": {"cairn": {
        "command": "cairn",
        "args": ["serve"],
        "env": {"CAIRN_WORKSPACE": str(tmp_path / "pinned-workspace")},
    }}}
    initial["unrelated"] = {"token": "secret-value"}
    path.write_text(json.dumps(initial), encoding="utf-8")
    _age(path)
    _patch_registration(monkeypatch, path, "claude-desktop", workspace_owned=False)

    actions = apply_doctor_fixes(str(tmp_path / "store.db"))

    assert len(actions) == 1
    assert actions[0]["action"] == "remove-unsupported-desktop"
    assert actions[0]["client"] == "claude-desktop"
    assert actions[0]["config"] == "config/config.json"
    assert "secret-value" not in json.dumps(actions)
    assert json.loads(path.read_text(encoding="utf-8")) == initial
    assert not path.with_suffix(".json.bak").exists()


def test_current_registration_is_untouched(tmp_path, monkeypatch):
    from cairn.agent_install import mcp_config_json
    from cairn.cli.system.doctor_fix import apply_doctor_fixes

    path = tmp_path / "config.json"
    initial = mcp_config_json()
    path.write_text(json.dumps(initial), encoding="utf-8")
    _age(path)
    _patch_registration(monkeypatch, path, "claude", workspace_owned=False)

    assert apply_doctor_fixes(str(tmp_path / "store.db")) == []
    assert json.loads(path.read_text(encoding="utf-8")) == initial
    assert not path.with_suffix(".json.bak").exists()


@pytest.mark.parametrize(
    "entry",
    [_STDIO_SEED, _SSE_SEED],
    ids=["stdio", "sse"],
)
def test_fresh_config_is_refused_without_backup(tmp_path, monkeypatch, entry):
    from cairn.cli.system.doctor_fix import apply_doctor_fixes

    path = tmp_path / "config.json"
    initial = {"mcpServers": {"cairn": copy.deepcopy(entry)}}
    path.write_text(json.dumps(initial), encoding="utf-8")
    _patch_registration(monkeypatch, path, "claude", workspace_owned=False)

    actions = apply_doctor_fixes(str(tmp_path / "store.db"))

    assert len(actions) == 1
    assert actions[0]["action"] == "refused-recent-write"
    assert actions[0]["client"] == "claude"
    assert actions[0]["config"] == "config/config.json"
    assert json.loads(path.read_text(encoding="utf-8")) == initial
    assert not path.with_suffix(".json.bak").exists()


@pytest.mark.parametrize(
    "entry",
    [_STDIO_SEED, _SSE_SEED],
    ids=["stdio", "sse"],
)
def test_workspace_owned_config_is_skipped_and_guided(tmp_path, monkeypatch, entry):
    from cairn.cli.system.doctor_fix import apply_doctor_fixes

    path = tmp_path / ".mcp.json"
    initial = {"mcpServers": {"cairn": copy.deepcopy(entry)}}
    path.write_text(json.dumps(initial), encoding="utf-8")
    _age(path)
    _patch_registration(monkeypatch, path, "claude", workspace_owned=True)

    actions = apply_doctor_fixes(str(tmp_path / "store.db"))

    assert len(actions) == 1
    assert actions[0]["action"] == "skipped-workspace-owned"
    assert actions[0]["client"] == "claude"
    assert actions[0]["config"] == "config/.mcp.json"
    assert json.loads(path.read_text(encoding="utf-8")) == initial
    assert not path.with_suffix(".json.bak").exists()
