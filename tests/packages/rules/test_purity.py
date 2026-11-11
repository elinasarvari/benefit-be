"""The rules package is pure: no file I/O, no clock reads (FR-004-19, KD-1, ADR-004-01)."""

from __future__ import annotations

import ast
from pathlib import Path

RULES = Path(__file__).parents[3] / "packages" / "rules"
FORBIDDEN_MODULES = {"pathlib", "os", "io", "shutil", "tempfile", "glob", "importlib.resources"}
FORBIDDEN_CALLS = {"open", "now", "today", "utcnow", "time", "monotonic", "read_text", "read_bytes"}


def test_FR_004_19_01_rules_package_does_no_file_io_or_clock_reads() -> None:
    offences: list[str] = []
    for path in sorted(RULES.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                offences += [
                    f"{path.name}: import {a.name}"
                    for a in node.names
                    if a.name in FORBIDDEN_MODULES
                ]
            elif isinstance(node, ast.ImportFrom) and node.module in FORBIDDEN_MODULES:
                offences.append(f"{path.name}: from {node.module}")
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name in FORBIDDEN_CALLS:
                    offences.append(f"{path.name}: call {name}")
    assert offences == []
