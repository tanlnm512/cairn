import json
from pathlib import Path
import pytest

from tests.fixtures.golden.regenerate import LANG_CONFIG, GOLDEN_DIR, normalise


@pytest.mark.parametrize("lang", list(LANG_CONFIG.keys()))
def test_parser_output_matches_golden(lang):
    """Ensure parser output for each supported language matches the golden snapshot."""
    parser_cls, filename = LANG_CONFIG[lang]
    sample_path = GOLDEN_DIR / lang / filename
    expected_path = GOLDEN_DIR / lang / "expected.json"

    assert sample_path.exists(), f"Sample fixture missing for {lang}"
    assert expected_path.exists(), f"Expected golden JSON missing for {lang}"

    parser = parser_cls()
    parsed = parser.parse(str(sample_path))
    actual = normalise(parsed)

    with open(expected_path, "r", encoding="utf-8") as fh:
        expected = json.load(fh)

    assert actual == expected, f"Parser output for {lang} diverged from golden snapshot"
