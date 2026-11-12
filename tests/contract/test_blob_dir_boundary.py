"""Files are stored only through BlobStore (KD-14, ADR-002-03; TC-47)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPS = ROOT / "apps"
PROVIDER = Path("apps/api/dependencies/documents.py")
FILE_CALLS = {"open", "read_bytes", "write_bytes", "read_text", "write_text", "mkdir"}


def _sources() -> list[Path]:
    return [p for p in APPS.rglob("*.py") if "__pycache__" not in p.parts]


def test_FR_002_17_apps_never_open_blob_dir_directly() -> None:
    offenders: list[str] = []
    for path in _sources():
        relative = path.relative_to(ROOT)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = {n.attr for n in [node] if isinstance(n, ast.Attribute)} | {
                n.id for n in [node] if isinstance(n, ast.Name)
            }
            if "BLOB_DIR" in names and relative != PROVIDER:
                offenders.append(f"{relative}:{getattr(node, 'lineno', 0)} reads BLOB_DIR")
            if "LocalBlobStore" in names and relative != PROVIDER:
                offenders.append(f"{relative}:{getattr(node, 'lineno', 0)} builds a LocalBlobStore")

    assert not offenders, "\n".join(offenders)


def test_FR_002_17_only_the_provider_reads_blob_dir_and_only_to_build_the_store() -> None:
    tree = ast.parse((ROOT / PROVIDER).read_text())
    uses = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and any(isinstance(arg, ast.Attribute) and arg.attr == "BLOB_DIR" for arg in ast.walk(node))
    ]
    assert uses, "the provider no longer builds the store from BLOB_DIR"
    for call in uses:
        assert isinstance(call.func, ast.Name) and call.func.id == "LocalBlobStore"


def test_FR_002_17_no_file_io_in_apps_api_documents_code() -> None:
    offenders = []
    for path in (APPS / "api").rglob("*.py"):
        if "documents" not in path.name:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if called in FILE_CALLS:
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)} calls {called}"
                    )
    assert not offenders, "\n".join(offenders)
