"""Tests read only files inside this repo, so CI (a bare checkout of benefit-be) sees what we see.

A test that climbs `Path(__file__).parents[N]` above the repo root passes inside the metarepo
checkout and fails in CI, which is how the 003 contract test broke. Contracts from the metarepo
are vendored under `tests/contract/<feature>/` instead.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS = REPO_ROOT / "tests"
CLIMB = re.compile(r"Path\(__file__\)(?:\.resolve\(\))?\.parents\[(\d+)\]")


def test_FR_000_16_no_test_reads_files_above_the_repo_root() -> None:
    escapes = []
    for source in sorted(TESTS.rglob("*.py")):
        for match in CLIMB.finditer(source.read_text()):
            depth = int(match.group(1))
            ancestors = source.resolve().parents
            target = ancestors[depth] if depth < len(ancestors) else Path("/")
            if not target.is_relative_to(REPO_ROOT):
                escapes.append(f"{source.relative_to(REPO_ROOT)}: parents[{depth}]")

    assert not escapes, f"tests reach outside the repo (vendor the file instead): {escapes}"
