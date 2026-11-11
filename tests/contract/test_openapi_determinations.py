"""The committed OpenAPI file carries the determinations contract (003; TC-115; ADR-003-05).

`tests/contract/eligibility_rules/rest-api.openapi.yaml` is a verbatim copy of
`specs/003-eligibility-rules/contracts/rest-api.openapi.yaml` in ai-immersion-metarepo, so this
repo's CI can check it without the metarepo (as `case_lifecycle/` does for 001). Update the copy
when the contract changes there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.export_openapi import OUTPUT

CONTRACT = Path(__file__).parent / "eligibility_rules" / "rest-api.openapi.yaml"
PATH = "/api/v1/cases/{case_id}/determinations"
SCHEMAS = ("DeterminationList", "DeterminationView", "RuleResult", "MissingItem")


def _load() -> tuple[dict[str, Any], dict[str, Any]]:
    if not CONTRACT.is_file():
        pytest.fail(f"contract not found at {CONTRACT}")
    contract = yaml.safe_load(CONTRACT.read_text())
    committed = json.loads(Path(OUTPUT).read_text())
    return contract, committed


def _resolve(schema: Any, document: dict[str, Any]) -> Any:
    """Inline `$ref`s to plain enums and drop prose, so two documents compare by shape only.

    FastAPI names every enum; the contract writes the values in place. Object refs stay refs.
    """
    if isinstance(schema, dict):
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            target = document.get(name, {})
            if "enum" in target and "properties" not in target:
                return {k: v for k, v in target.items() if k not in ("title", "description")}
            return {"$ref": name}
        return {k: _resolve(v, document) for k, v in schema.items() if k != "description"}
    if isinstance(schema, list):
        return [_resolve(v, document) for v in schema]
    return schema


def _normal(schema: dict[str, Any], document: dict[str, Any]) -> Any:
    """Compare nullable types as sets: `[string, 'null']` equals `anyOf` with a null branch."""
    resolved = _resolve(schema, document)

    def fold(node: Any) -> Any:
        if isinstance(node, dict):
            prose = ("title", "format", "default")  # pydantic adds a title next to `anyOf`
            if "anyOf" in node and len([k for k in node if k not in prose]) == 1:
                branches = [fold(b) for b in node["anyOf"]]
                kinds = sorted(
                    b.get("type") for b in branches if isinstance(b, dict) and "type" in b
                )
                if len(kinds) == len(branches) and all(isinstance(k, str) for k in kinds):
                    return {"type": kinds}
            folded = {
                k: fold(v) for k, v in node.items() if k not in ("title", "format", "default")
            }
            if isinstance(folded.get("type"), list):
                folded["type"] = sorted(folded["type"])
            return folded
        if isinstance(node, list):
            return [fold(v) for v in node]
        return node

    return fold(resolved)


def test_FR_003_23_committed_openapi_has_list_case_determinations_matching_contract() -> None:
    contract, committed = _load()

    operation = committed["paths"][PATH]["get"]
    expected = contract["paths"][PATH]["get"]
    assert operation["operationId"] == expected["operationId"] == "listCaseDeterminations"
    assert set(expected["responses"]) <= set(operation["responses"]), "status codes missing"
    ok = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok["$ref"].endswith("/DeterminationList")

    live = committed["components"]["schemas"]
    wanted = contract["components"]["schemas"]
    for name in SCHEMAS:
        assert name in live, f"schema {name} missing from openapi/openapi.json"
        assert set(wanted[name].get("required", [])) <= set(live[name].get("required", [])), name
        assert _normal(wanted[name].get("properties", {}), wanted) == _normal(
            live[name].get("properties", {}), live
        ), f"schema {name} differs from the contract"
        assert live[name].get("additionalProperties") is False, f"{name} must forbid extras"
