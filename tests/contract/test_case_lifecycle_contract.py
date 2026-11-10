"""Case lifecycle contracts (001; TC-66, TC-67, constitution VI).

The contract files in `tests/contract/case_lifecycle/` are verbatim copies of
`specs/001-case-lifecycle/contracts/` in ai-immersion-metarepo, so this repo's CI can check them
without the metarepo. Update the copies when the contract changes there.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from temporalio import activity, workflow

from apps.worker.main import WORKFLOWS, build_activities
from apps.worker.workflows.case import CaseWorkflow
from packages.platform.clock import SystemClock
from packages.platform.mongo import make_client
from packages.platform.settings import Settings
from scripts.export_openapi import OUTPUT

CONTRACTS = Path(__file__).parent / "case_lifecycle"
REST_CONTRACT = CONTRACTS / "rest-api.openapi.yaml"
WORKFLOW_CONTRACT = CONTRACTS / "case-workflow.md"
METHODS = ("get", "post", "put", "patch", "delete")


# --- OpenAPI helpers -----------------------------------------------------------------------------


def _resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref is None:
        return schema
    node: Any = document
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return _resolve(document, node)


def _enum_values(document: dict[str, Any], schema: dict[str, Any]) -> set[Any] | None:
    """Enum values of a property, looking through `$ref` and `anyOf: [..., null]`."""
    schema = _resolve(document, schema)
    if "enum" in schema:
        return {v for v in schema["enum"] if v is not None}
    for option in schema.get("anyOf", []):
        values = _enum_values(document, option)
        if values is not None:
            return values
    return None


def _max_length(document: dict[str, Any], schema: dict[str, Any]) -> int | None:
    schema = _resolve(document, schema)
    if "maxLength" in schema:
        return int(schema["maxLength"])
    for option in schema.get("anyOf", []):
        found = _max_length(document, option)
        if found is not None:
            return found
    return None


def _body_schema(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    return _resolve(document, schema)


def _success_schema(document: dict[str, Any], response: dict[str, Any]) -> dict[str, Any] | None:
    content = response.get("content", {}).get("application/json")
    return _resolve(document, content["schema"]) if content else None


def _compare_object(
    label: str,
    contract_doc: dict[str, Any],
    contract: dict[str, Any],
    generated_doc: dict[str, Any],
    generated: dict[str, Any],
    *,
    exact_enums: bool,
) -> list[str]:
    """Contract fields, required fields, enums and max lengths must hold in the generated schema.

    Request bodies need equal enums (the API accepts exactly what the contract says). Responses
    may carry more enum values than the contract lists, for values reserved for later features.
    """
    problems = []
    c_props, g_props = contract.get("properties", {}), generated.get("properties", {})
    problems += [f"{label}: field {name} missing" for name in sorted(c_props.keys() - g_props)]
    problems += [
        f"{label}: field {name} not in the contract" for name in sorted(g_props.keys() - c_props)
    ]
    missing_required = set(contract.get("required", [])) - set(generated.get("required", []))
    problems += [f"{label}: field {name} not required" for name in sorted(missing_required)]
    if contract.get("additionalProperties") is False and generated.get("additionalProperties"):
        problems.append(f"{label}: generated schema allows extra fields")
    for name in sorted(c_props.keys() & g_props.keys()):
        c_enum = _enum_values(contract_doc, c_props[name])
        g_enum = _enum_values(generated_doc, g_props[name])
        if c_enum is not None:
            if g_enum is None:
                problems.append(f"{label}.{name}: generated has no enum")
            elif exact_enums and c_enum != g_enum:
                problems.append(f"{label}.{name}: enum {sorted(g_enum)} != {sorted(c_enum)}")
            elif not c_enum <= g_enum:
                problems.append(f"{label}.{name}: missing enum values {sorted(c_enum - g_enum)}")
        c_max = _max_length(contract_doc, c_props[name])
        if c_max is not None and _max_length(generated_doc, g_props[name]) != c_max:
            problems.append(f"{label}.{name}: maxLength is not {c_max}")
    return problems


def contract_differences(contract: dict[str, Any], generated: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for path, operations in contract["paths"].items():
        if path not in generated["paths"]:
            problems.append(f"path missing: {path}")
            continue
        for method, c_op in operations.items():
            if method not in METHODS:
                continue
            g_op = generated["paths"][path].get(method)
            label = f"{method.upper()} {path}"
            if g_op is None:
                problems.append(f"operation missing: {label}")
                continue
            if g_op.get("operationId") != c_op["operationId"]:
                problems.append(f"{label}: operationId {g_op.get('operationId')}")
            security = g_op.get("security", generated.get("security"))
            if c_op.get("security") and not security:
                problems.append(f"{label}: no session security")
            missing = set(c_op["responses"]) - set(g_op["responses"])
            problems += [f"{label}: response {code} missing" for code in sorted(missing)]
            c_params = {p["name"] for p in c_op.get("parameters", [])}
            g_params = {p["name"] for p in g_op.get("parameters", [])}
            if c_params != g_params:
                problems.append(f"{label}: parameters {sorted(g_params)} != {sorted(c_params)}")
            if "requestBody" in c_op:
                if "requestBody" not in g_op:
                    problems.append(f"{label}: request body missing")
                else:
                    problems += _compare_object(
                        f"{label} body",
                        contract,
                        _body_schema(contract, c_op),
                        generated,
                        _body_schema(generated, g_op),
                        exact_enums=True,
                    )
            for code, c_response in c_op["responses"].items():
                if not code.startswith("2") or code not in g_op["responses"]:
                    continue
                c_schema = _success_schema(contract, c_response)
                g_schema = _success_schema(generated, g_op["responses"][code])
                if c_schema is None:
                    continue
                if g_schema is None:
                    problems.append(f"{label} {code}: response body missing")
                    continue
                problems += _compare_object(
                    f"{label} {code}", contract, c_schema, generated, g_schema, exact_enums=False
                )
    return problems


def test_FR_001_01_openapi_matches_case_lifecycle_contract() -> None:
    contract = yaml.safe_load(REST_CONTRACT.read_text())
    generated = json.loads(Path(OUTPUT).read_text())

    problems = contract_differences(contract, generated)

    assert not problems, (
        "openapi/openapi.json does not match contracts/rest-api.openapi.yaml "
        "(run `make openapi`, then reconcile):\n" + "\n".join(problems)
    )


# --- Workflow names (TC-67) ----------------------------------------------------------------------


def _section(markdown: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}.*?$(.*?)(?=^## |\Z)", markdown, re.M | re.S)
    assert match is not None, f"section {heading!r} missing from case-workflow.md"
    return match.group(1)


def _table_names(section: str) -> set[str]:
    """First-column names of a markdown table: `| `name` | ... |`."""
    return set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, re.M))


def contract_names() -> dict[str, set[str]]:
    text = WORKFLOW_CONTRACT.read_text()
    workflow_type = re.search(r"\*\*Type:\*\*\s*`([A-Za-z]+)`", _section(text, "Workflow"))
    assert workflow_type is not None
    return {
        "workflow": {workflow_type.group(1)},
        "signals": _table_names(_section(text, "Signals")),
        "queries": _table_names(_section(text, "Queries")),
        "activities": _table_names(_section(text, "Activities")),
    }


def registered_activity_names(settings: Settings) -> set[str]:
    mongo = make_client(settings.MONGO_URL_APP.get_secret_value())  # never connected here
    activities = build_activities(settings, SystemClock(), mongo)
    names = set()
    for fn in activities:
        definition = activity._Definition.from_callable(fn)
        assert definition is not None and definition.name is not None
        names.add(definition.name)
    return names


def test_FR_001_07_registered_workflow_names_match_contract(settings: Settings) -> None:
    contract = contract_names()
    definition = workflow._Definition.must_from_class(CaseWorkflow)

    registered_types = {workflow._Definition.must_from_class(w).name for w in WORKFLOWS}
    signals = {name for name in definition.signals if name is not None}
    queries = {name for name in definition.queries if name is not None}

    assert contract["workflow"] <= registered_types
    assert definition.name in contract["workflow"]
    assert signals == contract["signals"]
    assert queries == contract["queries"]
    assert contract["activities"] <= registered_activity_names(settings)
