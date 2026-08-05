"""Guard the layer dependency direction.

The whole point of the src/ layout is that each layer only depends on the
ones below it:

    core → ingest → analytics → predict → optimize → backtest → api

An import that points *upward* means a module is in the wrong layer. That is
how `tools/` ended up importing from `backtest/` and `api/` — one exception at
a time, each individually reasonable. This test makes the next one fail loudly
instead of quietly.
"""

import re
from pathlib import Path

import pytest

LAYERS = ["core", "ingest", "analytics", "predict", "optimize", "backtest", "api"]
RANK = {name: i for i, name in enumerate(LAYERS)}

SRC = Path(__file__).resolve().parents[1] / "src"
IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+src\.([a-z_]+)", re.MULTILINE)


def _source_files():
    for path in sorted(SRC.rglob("*.py")):
        parts = path.relative_to(SRC).parts
        if len(parts) >= 2 and parts[0] in RANK:
            yield path


def _upward_imports(path: Path) -> list[tuple[str, str]]:
    layer = path.relative_to(SRC).parts[0]
    found = []
    for match in IMPORT_RE.finditer(path.read_text()):
        dep = match.group(1)
        if dep in RANK and RANK[dep] > RANK[layer]:
            found.append((layer, dep))
    return found


@pytest.mark.parametrize(
    "path", list(_source_files()), ids=lambda p: str(p.relative_to(SRC))
)
def test_no_upward_imports(path: Path):
    violations = _upward_imports(path)
    assert not violations, (
        f"{path.relative_to(SRC)} imports upward: "
        + ", ".join(f"{lo} -> {hi}" for lo, hi in violations)
        + f"\nLayer order is {' -> '.join(LAYERS)}."
    )


def test_every_layer_exists():
    """A renamed or deleted layer should update this test, not silently pass."""
    missing = [name for name in LAYERS if not (SRC / name).is_dir()]
    assert not missing, f"Layers named in this test no longer exist: {missing}"
