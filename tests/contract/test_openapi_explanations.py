"""The committed OpenAPI file lists both explanation routes with real responses (004; TC-38)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.export_openapi import OUTPUT, build_schema, render

ROUTES = ("/api/v1/cases/{case_id}/explanations", "/api/v1/me/history")


def test_FR_004_15_10_openapi_lists_both_routes_and_matches_generated_file() -> None:
    committed = json.loads(Path(OUTPUT).read_text())

    for path in ROUTES:
        responses = committed["paths"][path]["get"]["responses"]
        assert "200" in responses, path
        assert "501" not in responses, f"{path} is still a stub"
    assert "404" in committed["paths"][ROUTES[0]]["get"]["responses"]
    assert Path(OUTPUT).read_text() == render(build_schema())
