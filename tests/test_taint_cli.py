"""`cairn taint` CLI contract: path rendering, precision flags, and errors.

Each test builds a self-contained mini workspace (tmp_path) with a known
source-to-sink flow, indexes it, and drives the command through CliRunner.
Hermeticity per CONSTITUTION C-04: `cairn.cli` is imported lazily inside
the tests, `CAIRN_WORKSPACE` scopes config resolution to the tmp workspace,
and every invocation passes `--db` so no store outside the sandbox is read
or written.
"""

from __future__ import annotations

from pathlib import Path

from cairn.graph.builder import build_graph

_SQL_FLOW_APP = '''\
def handle_request(urlopen, db):
    raw = urlopen("/requests/current")
    return validate_input(raw, db)


def validate_input(value, db):
    return run_query(value, db)


def run_query(query, db):
    cursor = db.cursor()
    return cursor.execute(query)
'''

_AMBIGUOUS_APP = '''\
def handle_upload(urlopen, db):
    payload = urlopen("/uploads/current")
    return transform(payload, db)


def run_migration(statement, db):
    cursor = db.cursor()
    return cursor.execute(statement)
'''

_AMBIGUOUS_TRANSFORMS = '''\
def transform(payload, db):
    return run_migration(payload, db)
'''

_CLEAN_APP = '''\
def handle_request(urlopen):
    raw = urlopen("/requests/current")
    return format_report(raw)


def format_report(raw):
    return "report: " + str(raw)
'''

_CUSTOM_CONFIG_APP = '''\
def consume_job(queue):
    job = receive_job(queue)
    return render_invoice(job)


def render_invoice(job):
    return render_template(job)
'''

_DEEP_CHAIN_APP = '''\
def entry(urlopen):
    value = urlopen("/in")
    return middle(value)


def middle(value):
    return terminus(value)


def terminus(value):
    cursor = db().cursor()
    return cursor.execute(value)
'''

_CUSTOM_CONFIG_JSON = """\
{"taint": {"sources": {"queue-msg": ["receive_job"]},
           "sinks": {"render": ["render_template"]}}}
"""


def _make_ws(tmp_path: Path, sources: dict[str, str], config_json: str = "") -> Path:
    ws = tmp_path / "ws"
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)
    for name, text in sources.items():
        (repo / name).write_text(text, encoding="utf-8")
    if config_json:
        (ws / "cairn.json").write_text(config_json, encoding="utf-8")
    return ws


def _build_db(ws: Path, tmp_path: Path) -> Path:
    db = tmp_path / "graph.kg"
    build_graph(workspace=str(ws), db_path=str(db))
    return db


def _invoke_taint(ws: Path, db: Path, *args):
    from click.testing import CliRunner

    from cairn.cli import main

    return CliRunner().invoke(
        main,
        ["taint", "--db", str(db), *args],
        env={"CAIRN_WORKSPACE": str(ws)},
    )


def test_exact_path_prints_every_hop_with_resolution_label(tmp_path):
    ws = _make_ws(tmp_path, {"app.py": _SQL_FLOW_APP})
    db = _build_db(ws, tmp_path)
    result = _invoke_taint(ws, db, "--from", "http", "--to", "sql")
    assert result.exit_code == 0
    for symbol in ("handle_request", "validate_input", "run_query"):
        assert symbol in result.output
    assert result.output.count("[exact]") == 3


def test_fuzzy_leaves_exact_output_unchanged(tmp_path):
    ws = _make_ws(tmp_path, {"app.py": _SQL_FLOW_APP})
    db = _build_db(ws, tmp_path)
    result = _invoke_taint(ws, db, "--from", "http", "--to", "sql", "--fuzzy")
    assert result.exit_code == 0
    assert result.output.count("[exact]") == 3
    assert "[ambiguous]" not in result.output


def test_default_stops_at_ambiguous_hop_and_fuzzy_crosses_it(tmp_path):
    ws = _make_ws(
        tmp_path,
        {"app.py": _AMBIGUOUS_APP, "transforms_a.py": _AMBIGUOUS_TRANSFORMS,
         "transforms_b.py": _AMBIGUOUS_TRANSFORMS},
    )
    db = _build_db(ws, tmp_path)

    exact = _invoke_taint(ws, db, "--from", "http", "--to", "sql")
    assert exact.exit_code != 0
    assert "run_migration" not in exact.output

    fuzzy = _invoke_taint(ws, db, "--from", "http", "--to", "sql", "--fuzzy")
    assert fuzzy.exit_code == 0
    assert "handle_upload" in fuzzy.output
    assert "run_migration" in fuzzy.output
    assert "[ambiguous]" in fuzzy.output


def test_clean_workspace_yields_no_path_default_and_fuzzy(tmp_path):
    ws = _make_ws(tmp_path, {"app.py": _CLEAN_APP})
    db = _build_db(ws, tmp_path)
    for args in (("--from", "http", "--to", "sql"),
                 ("--from", "http", "--to", "sql", "--fuzzy")):
        result = _invoke_taint(ws, db, *args)
        assert result.exit_code != 0
        assert "No taint paths" in result.output
        assert "format_report" not in result.output


def test_unknown_pattern_reports_error_without_traceback(tmp_path):
    ws = _make_ws(tmp_path, {"app.py": _SQL_FLOW_APP})
    db = _build_db(ws, tmp_path)
    result = _invoke_taint(ws, db, "--from", "no-such-source", "--to", "sql")
    assert result.exit_code != 0
    assert "unknown source pattern" in result.output
    assert "no-such-source" in result.output
    assert "Traceback" not in result.output


def test_config_declared_categories_trace_without_defaults(tmp_path):
    ws = _make_ws(
        tmp_path, {"app.py": _CUSTOM_CONFIG_APP}, config_json=_CUSTOM_CONFIG_JSON
    )
    db = _build_db(ws, tmp_path)

    custom = _invoke_taint(ws, db, "--from", "queue-msg", "--to", "render")
    assert custom.exit_code == 0
    assert "consume_job" in custom.output
    assert "render_invoice" in custom.output

    defaults = _invoke_taint(ws, db, "--from", "http", "--to", "sql")
    assert defaults.exit_code != 0
    assert "consume_job" not in defaults.output


def test_max_depth_bounds_traversal_and_validates_input(tmp_path):
    ws = _make_ws(tmp_path, {"app.py": _DEEP_CHAIN_APP})
    db = _build_db(ws, tmp_path)

    shallow = _invoke_taint(ws, db, "--from", "http", "--to", "sql", "--max-depth", "1")
    assert shallow.exit_code != 0
    assert "terminus" not in shallow.output

    deep = _invoke_taint(ws, db, "--from", "http", "--to", "sql", "--max-depth", "2")
    assert deep.exit_code == 0
    assert "entry" in deep.output
    assert "terminus" in deep.output

    invalid = _invoke_taint(ws, db, "--from", "http", "--to", "sql", "--max-depth", "0")
    assert invalid.exit_code != 0
    assert "--max-depth" in invalid.output
