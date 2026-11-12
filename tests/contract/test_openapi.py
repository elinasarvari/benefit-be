from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.export_openapi import OUTPUT, build_schema, render


def describe_difference(committed: dict[str, Any], live: dict[str, Any]) -> str:
    lines = []
    for section in ("paths",):
        old, new = committed.get(section, {}), live.get(section, {})
        lines += [f"{section} added: {k}" for k in sorted(new.keys() - old.keys())]
        lines += [f"{section} removed: {k}" for k in sorted(old.keys() - new.keys())]
        lines += [
            f"{section} changed: {k}" for k in sorted(old.keys() & new.keys()) if old[k] != new[k]
        ]
    old_s = committed.get("components", {}).get("schemas", {})
    new_s = live.get("components", {}).get("schemas", {})
    lines += [f"schema added: {k}" for k in sorted(new_s.keys() - old_s.keys())]
    lines += [f"schema removed: {k}" for k in sorted(old_s.keys() - new_s.keys())]
    lines += [
        f"schema changed: {k}" for k in sorted(old_s.keys() & new_s.keys()) if old_s[k] != new_s[k]
    ]
    return "\n".join(lines) or "documents differ outside paths and schemas"


def test_FR_000_18_live_schema_matches_committed_and_names_diff() -> None:
    committed = json.loads(Path(OUTPUT).read_text())
    live = json.loads(render(build_schema()))

    assert committed == live, (
        "openapi/openapi.json is out of date, run `make openapi`:\n"
        + describe_difference(committed, live)
    )


def test_FR_000_26_committed_openapi_file_is_current_and_sorted() -> None:
    assert Path(OUTPUT).read_text() == render(build_schema())


def test_FR_000_18_every_error_response_uses_the_error_envelope() -> None:
    schema = json.loads(Path(OUTPUT).read_text())
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for status, response in operation["responses"].items():
                if (
                    status.startswith(("4", "5"))
                    and status != "503"
                    or status == "503"
                    and "ready" not in path
                ):
                    ref = response["content"]["application/json"]["schema"]["$ref"]
                    assert ref.endswith("/ErrorEnvelope"), f"{method.upper()} {path} {status}"


def test_FR_002_17_stub_routes_no_longer_501_and_openapi_current() -> None:
    committed = json.loads(Path(OUTPUT).read_text())
    live = json.loads(render(build_schema()))
    routes = (
        ("post", "/api/v1/cases/{case_id}/documents"),
        ("put", "/api/v1/cases/{case_id}/documents/{document_id}/content"),
        ("get", "/api/v1/cases/{case_id}/verification"),
    )
    for document in (committed, live):
        for method, path in routes:
            responses = document["paths"][path][method]["responses"]
            assert "501" not in responses, f"{method.upper()} {path} still documents 501"
    content = live["paths"][routes[1][1]]["put"]["responses"]
    assert {"204", "403", "404", "413", "415"} <= content.keys()
    assert committed["paths"] == live["paths"], "run `make openapi`"
    action = live["components"]["schemas"]["ReviewActionBody"]["properties"]
    assert {"corrected_value", "finding_id"} <= action.keys()
