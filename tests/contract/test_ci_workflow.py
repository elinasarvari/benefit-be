from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"
REQUIRED_JOBS = {"static", "unit", "workflow", "contract", "integration", "e2e"}


def load(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text())
    return loaded


def run_commands(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_FR_000_16_ci_runs_every_required_job() -> None:
    ci = load("ci.yml")

    assert REQUIRED_JOBS <= set(ci["jobs"])
    static = run_commands(ci["jobs"]["static"])
    for command in ("make lint", "make typecheck", "pip-audit", "gitleaks detect"):
        assert command in static
    assert "make test-unit" in run_commands(ci["jobs"]["unit"])
    assert "make test-workflow" in run_commands(ci["jobs"]["workflow"])
    assert "make test-contract" in run_commands(ci["jobs"]["contract"])
    assert "make test-integration" in run_commands(ci["jobs"]["integration"])
    e2e = run_commands(ci["jobs"]["e2e"])
    assert 'pytest -m "stack or api"' in e2e
    assert "pnpm e2e" in e2e


def test_FR_000_20_ci_never_uses_a_live_model() -> None:
    env = load("ci.yml")["env"]

    assert env["LLM_MODE"] == "fake"
    assert env["APP_ENV"] == "test"
    assert str(env["TRACING_ENABLED"]).lower() == "false"
    assert "ANTHROPIC_API_KEY" not in (WORKFLOWS / "ci.yml").read_text()


def test_every_job_has_a_timeout_and_every_action_is_pinned_by_sha() -> None:
    for name in ("ci.yml", "nightly.yml"):
        workflow = load(name)
        assert all("timeout-minutes" in job for job in workflow["jobs"].values()), name
        for uses in re.findall(r"uses:\s*(\S+)", (WORKFLOWS / name).read_text()):
            if not uses.startswith("./"):
                assert re.search(r"@[0-9a-f]{40}$", uses), f"{name}: {uses} is not pinned by SHA"
