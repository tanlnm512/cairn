import tempfile
from pathlib import Path

from codegraph.graph.embeddings import chunk_for_symbol
from codegraph.graph.schema import get_db
from codegraph.parsers.python_parser import PythonParser


def test_chunk_variant_shapes():
    row = {
        "name": "process",
        "kind": "method",
        "qualified_name": "User.process",
        "docstring": "Process user data.",
        "parameters": '[{"name": "input_str", "type": "str"}]',
        "return_type": "str",
        "body": "return f'processed_{input_str}'",
    }
    sig = "def process(self, input_str: str) -> str:\n    pass"

    chunk_a = chunk_for_symbol(row, signature=sig, variant="A")
    assert "method User.process" in chunk_a
    assert "def process" in chunk_a

    chunk_b = chunk_for_symbol(row, signature=sig, variant="B")
    assert "Signature: def process" in chunk_b
    assert "Parameters: [{" in chunk_b
    assert "Return Type: str" in chunk_b
    assert "Docstring: Process user data." in chunk_b

    chunk_c = chunk_for_symbol(row, signature=sig, variant="C")
    assert "Body:" in chunk_c


def test_schema_migration_parameters_return_type():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = get_db(db_path)
        cursor = conn.cursor()

        # Verify columns exist
        cols = [r[1] for r in cursor.execute("PRAGMA table_info(symbols)").fetchall()]
        assert "parameters" in cols
        assert "return_type" in cols
        conn.close()


def test_python_parser_extracts_docstring():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as fh:
        fh.write('''class Sample:
    """Class docstring."""

    def action(self):
        """Method docstring."""
        return 42
''')
        fh_path = fh.name

    try:
        parser = PythonParser()
        parsed = parser.parse(fh_path)
        class_sym = next(s for s in parsed.symbols if s.name == "Sample")
        method_sym = next(s for s in parsed.symbols if s.name == "action")

        assert class_sym.docstring == "Class docstring."
        assert method_sym.docstring == "Method docstring."
    finally:
        Path(fh_path).unlink(missing_ok=True)
