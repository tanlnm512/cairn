"""Synthetic corpus generator for the benchmark suites.

Produces a deterministic, parameterized Python source tree that exercises the
full build pipeline (parse → insert → resolve → embed) with a realistic-ish
call graph: classes with methods, cross-file calls, and imports. Seeded so
runs across machines/sessions are directly comparable — critical for
regression detection, where a non-deterministic corpus would make the timing
signal noisy.

The generated files are intentionally simple-but-interconnected: each module
imports a couple of siblings and calls methods on them, so the resolver has
real edges to follow and the query battery (get_callers/impact_analysis) has
non-trivial blast radii rather than a flat list of isolated symbols.
"""
from __future__ import annotations

import random
from pathlib import Path

# A seeded corpus is comparable across runs/machines. The default seed is fixed
# so two ``cg bench`` invocations generate byte-identical source trees.
DEFAULT_SEED = 0xC0DE


def generate_corpus(
    root: Path,
    n_files: int,
    *,
    complexity: str = "medium",
    seed: int = DEFAULT_SEED,
) -> Path:
    """Generate ``n_files`` synthetic Python modules under ``root``.

    Creates ``root/<repo>/`` with a ``.git`` marker (so the scanner recognizes
    the directory as a repo — see ``scanner.discover_repos``) plus ``n_files``
    ``module_XXXX.py`` files. Returns the repo path.

    ``complexity`` controls the per-file structure:
      - ``low``:    ~3 classes, ~3 methods each, minimal cross-file calls.
      - ``medium``: ~5 classes, ~5 methods each, each file calls into 2-3 siblings.
      - ``high``:   ~8 classes, ~8 methods each, dense cross-file call web.

    The generator is deterministic given the same ``seed`` and ``n_files``.
    """
    rng = random.Random(seed)
    profiles = {
        "low": (3, 3, 3),
        "medium": (5, 5, 3),
        "high": (8, 8, 6),
    }
    n_classes, n_methods, n_calls = profiles.get(complexity, profiles["medium"])

    repo = root / "benchrepo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)  # scanner marker

    module_names = [f"module_{i:04d}" for i in range(n_files)]

    for idx, mod in enumerate(module_names):
        lines: list[str] = []
        # Import a couple of sibling modules so cross-file edges exist.
        if n_files > 1:
            siblings = rng.sample(
                [m for j, m in enumerate(module_names) if j != idx],
                k=min(n_calls, n_files - 1),
            )
            for sib in siblings:
                lines.append(f"from . import {sib}")
            lines.append("")

        for ci in range(n_classes):
            class_name = f"Cls{idx:04d}_{ci}"
            lines.append(f"class {class_name}:")
            lines.append(f'    """Generated class {class_name} for benchmark corpus."""')
            for mi in range(n_methods):
                meth = f"method_{mi}"
                lines.append(f"    def {meth}(self):")
                lines.append(f'        """Method {meth} of {class_name}."""')
                body_lines = ["        x = 0"]
                # Calls into imported siblings + local siblings — real edges.
                for sib in rng.sample(siblings, k=min(2, len(siblings))) if n_files > 1 else []:
                    sib_cls = f"Cls{module_names.index(sib):04d}_{rng.randrange(n_classes)}"
                    sib_meth = f"method_{rng.randrange(n_methods)}"
                    body_lines.append(f"        {sib}.{sib_cls}().{sib_meth}()")
                if ci > 0:
                    prev_cls = f"Cls{idx:04d}_{rng.randrange(ci)}"
                    prev_meth = f"method_{rng.randrange(n_methods)}"
                    body_lines.append(f"        {prev_cls}().{prev_meth}()")
                body_lines.append("        return x")
                lines.extend(body_lines)
                lines.append("")
            lines.append("")
        (repo / f"{mod}.py").write_text("\n".join(lines), encoding="utf-8")

    # An __init__.py makes the package importable for the relative imports
    # above to be syntactically valid (the parser reads them as import edges
    # regardless, but well-formed source avoids parse-error noise in verbose
    # builds).
    (repo / "__init__.py").write_text("", encoding="utf-8")
    return repo


def corpus_stats(repo: Path) -> dict:
    """Quick counts for a generated corpus (files, lines, bytes)."""
    py_files = list(repo.rglob("*.py"))
    lines = sum(sum(1 for _ in p.open(encoding="utf-8")) for p in py_files)
    bytes_ = sum(p.stat().st_size for p in py_files)
    return {"files": len(py_files), "lines": lines, "bytes": bytes_}
