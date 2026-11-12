"""Rules stay pure and are imported only where the architecture allows (FR-003-08, FR-003-22)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "packages" / "rules"
SEEDS = ROOT / "db" / "seeds"
EXCLUDED_DIRS = {".venv", "node_modules", "__pycache__", ".git", "generated"}
ALLOWED_CONSUMERS = ("apps/worker/activities/", "db/seeds/", "evals/", "tests/")
FORBIDDEN_MODULES = {
    "os", "sys", "io", "time", "random", "secrets", "logging", "structlog", "socket",
    "subprocess", "pathlib", "shutil", "tempfile", "asyncio", "threading", "http", "urllib",
    "requests", "httpx", "pymongo", "motor", "temporalio", "numpy",
}  # fmt: skip
CLOCK_CALLS = {"now", "today", "utcnow", "time", "monotonic", "perf_counter"}


def _python_files(base: Path) -> list[Path]:
    return [p for p in base.rglob("*.py") if not EXCLUDED_DIRS & set(p.relative_to(ROOT).parts)]


def _imports(path: Path) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.module, node.lineno))
    return found


def test_FR_003_08_rules_package_imports_no_io_clock_random_or_logging() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{line} imports {module}"
        for path in _python_files(RULES)
        for module, line in _imports(path)
        if module.split(".")[0] in FORBIDDEN_MODULES
    ]

    assert not offenders, offenders


def test_FR_002_23_rules_purity_covers_verification() -> None:
    path = RULES / "verification.py"
    tree = ast.parse(path.read_text())
    offenders = [
        f"verification.py:{line} imports {module}"
        for module, line in _imports(path)
        if module.split(".")[0] in FORBIDDEN_MODULES
    ]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            owner_name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")
            if node.func.attr in CLOCK_CALLS and owner_name in {"datetime", "date", "time"}:
                offenders.append(f"verification.py:{node.lineno} {owner_name}.{node.func.attr}")
        if isinstance(node, ast.Name) and node.id in {"random", "open", "print"}:
            offenders.append(f"verification.py:{node.lineno} uses {node.id}")

    assert path.is_file()
    assert not offenders, offenders


def test_FR_003_08_rules_imported_only_by_worker_activities_seeds_and_evals() -> None:
    offenders = [
        f"{rel}:{line} imports {module}"
        for path in _python_files(ROOT)
        if not path.is_relative_to(RULES)
        for rel in [path.relative_to(ROOT).as_posix()]
        for module, line in _imports(path)
        if (module == "packages.rules" or module.startswith("packages.rules."))
        and not rel.startswith(ALLOWED_CONSUMERS)
    ]

    assert not offenders, offenders


def test_FR_003_22_seeds_never_read_wall_clock() -> None:
    offenders: list[str] = []
    for path in _python_files(SEEDS):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            owner = node.func.value
            owner_name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")
            if node.func.attr in CLOCK_CALLS and owner_name in {"datetime", "date", "time"}:
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} {owner_name}.{node.func.attr}"
                )

    assert not offenders, offenders
