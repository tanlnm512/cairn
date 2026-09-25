"""The installed AGENTS.md template tracks the repository's own copy."""
import re
from pathlib import Path

from cairn.agent_install import _agents_instructions


def test_template_layer_names_match_root_agents_md():
    """The template's tool-layer line equals the repo AGENTS.md line."""
    pattern = re.compile(r"- \d+ tools across \d+ layers: .+")
    root = pattern.search(
        (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(
            encoding="utf-8"
        )
    )
    template = pattern.search(_agents_instructions())
    assert root, "root AGENTS.md carries no tool-layer line"
    assert template, "template carries no tool-layer line"
    assert template.group(0).strip() == root.group(0).strip()
