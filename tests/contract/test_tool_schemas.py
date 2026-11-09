"""Agent tool schemas must match the agreed snapshots (FR-000-18).

Feature 005 registers the MCP tools in `packages/mcp_servers/registry.py` (a `tool_schemas()`
function returning `{"<server>/<tool>": {"input": ..., "output": ...}}`) and commits one
snapshot per tool in `tests/contract/tool_schemas/<server>/<tool>.json`. Until then there are
no tools and no snapshots, and this test passes because the two sets are equal (both empty).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

SNAPSHOTS = Path(__file__).parent / "tool_schemas"


def registered() -> dict[str, dict[str, Any]]:
    try:
        registry = importlib.import_module("packages.mcp_servers.registry")
    except ModuleNotFoundError:
        return {}
    schemas: dict[str, dict[str, Any]] = registry.tool_schemas()
    return schemas


def snapshots() -> dict[str, dict[str, Any]]:
    return {
        f"{path.parent.name}/{path.stem}": json.loads(path.read_text())
        for path in SNAPSHOTS.glob("*/*.json")
    }


def test_FR_000_18_tool_schemas_match_snapshots() -> None:
    live, saved = registered(), snapshots()

    assert live.keys() == saved.keys(), (
        f"tools without a snapshot: {sorted(live.keys() - saved.keys())}; "
        f"snapshots without a tool: {sorted(saved.keys() - live.keys())}"
    )
    changed = [name for name in live if live[name] != saved[name]]
    assert not changed, f"tool schemas changed, update the contract PR: {changed}"
