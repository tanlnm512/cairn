"""The raw ``ingest`` key in cairn.json (FR-010, D-005)."""
from __future__ import annotations

import json

from cairn.graph.config import load_config


def _write_config(tmp_path, payload):
    (tmp_path / "cairn.json").write_text(json.dumps(payload), encoding="utf-8")


def test_ingest_section_parses_raw(tmp_path):
    section = {
        "classification": {"decision-dirs": ["docs/choices"]},
        "skip": {"add": ["notes/"]},
    }
    _write_config(tmp_path, {"ingest": section})

    assert load_config(tmp_path).ingest == section


def test_missing_file_and_missing_key_default_to_empty(tmp_path):
    assert load_config(tmp_path).ingest == {}
    _write_config(tmp_path, {"exclude": ["build/"]})
    assert load_config(tmp_path).ingest == {}


def test_malformed_ingest_section_is_ignored(tmp_path, capsys):
    for bad in (["not", "a", "dict"], "nope", 42, None):
        _write_config(tmp_path, {"ingest": bad})
        assert load_config(tmp_path).ingest == {}
    assert "must be a JSON object" in capsys.readouterr().err


def test_ingest_coexists_with_existing_keys(tmp_path):
    _write_config(
        tmp_path,
        {
            "exclude": ["build/"],
            "repo_namespaces": {"com.example.sdk": "sdk"},
            "ingest": {"skip": {"add": ["changelogs/"]}},
        },
    )

    cfg = load_config(tmp_path)
    assert cfg.exclude == ["build/"]
    assert cfg.repo_namespaces == {"com.example.sdk": "sdk"}
    assert cfg.ingest == {"skip": {"add": ["changelogs/"]}}


def _w(root, rel, text):
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, "utf-8")


# --- Workspace overrides layered over built-ins (T015, FR-010/FR-012) ---


def _stage(tmp_path, monkeypatch, cairn_json=None, scan=False):
    import json as _json
    from cairn.knowledge.ingest import run_ingest

    monkeypatch.chdir(tmp_path)
    if cairn_json is not None:
        (tmp_path / "cairn.json").write_text(_json.dumps(cairn_json), "utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    if scan:
        run = lambda: run_ingest(
            files=[], dirs=[], repos=[tmp_path], outbox=tmp_path / "outbox"
        )
    else:
        run = lambda: run_ingest(files=[], dirs=[docs], outbox=tmp_path / "outbox")
    return docs, run


def test_classification_override_flips_doc_type(tmp_path, monkeypatch):
    docs, run = _stage(
        tmp_path, monkeypatch,
        {"ingest": {"classification": {"postmortem": "business-rule"}}},
    )
    _w(docs, "pm.md", "---\ntitle: Postmortem: outage\nstatus: accepted\n---\nWhat broke.")

    manifest = run()
    row = manifest["rows"][0]
    assert row["doc_type"] == "business-rule"
    assert "knowledge/business-rule/" in row["concept_id"]


def test_skip_disable_readmits_changelogs(tmp_path, monkeypatch):
    docs, run = _stage(
        tmp_path, monkeypatch,
        {"ingest": {"skip": {"disable": ["changelogs"]}}}, scan=True,
    )
    _w(docs, "changelogs/2026.md", "---\ntitle: Changelog 2026\nstatus: accepted\n---\nChanges.")

    manifest = run()
    assert manifest["counts"]["accepted"] == 1
    assert manifest["counts"]["skipped"] == 0


def test_skip_add_extends_the_skip_list(tmp_path, monkeypatch):
    docs, run = _stage(
        tmp_path, monkeypatch,
        {"ingest": {"skip": {"add": ["scratch/"]}}}, scan=True,
    )
    _w(docs, "scratch/notes.md", "---\ntitle: Scratch\nstatus: accepted\n---\nNotes.")
    _w(docs, "kept.md", "---\ntitle: Kept\nstatus: accepted\n---\nKept.")

    manifest = run()
    assert manifest["counts"]["accepted"] == 1
    skips = {r["source_path"]: r["skip"] for r in manifest["rows"] if "skip" in r}
    assert skips[f"{tmp_path.name}/docs/scratch/notes.md"] == "skip-list: scratch/ (workspace)"


def test_bare_workspace_runs_on_defaults(tmp_path, monkeypatch):
    docs, run = _stage(tmp_path, monkeypatch, cairn_json=None, scan=True)
    _w(docs, "adr.md", "---\ntitle: Use events\nstatus: accepted\n---\n# ADR: Use events\nBody.")
    _w(docs, "drafts/wip.md", "---\nstatus: draft\n---\nWIP.")

    manifest = run()
    assert manifest["counts"]["accepted"] == 1
    assert manifest["counts"]["skipped"] == 1
    row = manifest["rows"][0]
    assert row["doc_type"] == "decision"
