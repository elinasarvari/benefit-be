"""Run every check CI runs, in both repos, and print one table (`make verify`).

It mirrors the jobs in .github/workflows/ci.yml: static, unit, workflow, contract, integration and
e2e for benefit-be, and static, unit, contract, build and e2e for benefit-fe. It keeps going after a
failure so one run shows everything that is wrong, writes each stage's output to
.data/verify/<stage>.log, and exits non-zero unless every stage passed.

Statuses: PASS, FAIL, BLOCKED (a known external step, such as pushing the branch, is missing) and
INCONCLUSIVE (the check could not run, for example because the network is down).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess  # noqa: S404 - runs the project's own check commands
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BE = Path(__file__).resolve().parent.parent
FE = Path(os.environ.get("BENEFIT_FE_DIR", BE.parent / "benefit-fe")).resolve()
LOGS = BE / ".data" / "verify"
STACK_ENV = {"TRACING_ENABLED": "false"}
OFFLINE_MARKERS = ("NameResolutionError", "Failed to resolve", "ConnectionError", "ENOTFOUND")


@dataclass
class Result:
    name: str
    status: str
    seconds: float
    note: str = ""


def run(command: str, cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    merged = {**os.environ, **(env or {})}
    done = subprocess.run(  # noqa: S603 - commands are fixed strings below
        shlex.split(command), cwd=cwd, env=merged, capture_output=True, text=True, check=False
    )
    return done.returncode, done.stdout + done.stderr


def log_path(name: str) -> Path:
    return LOGS / (name.replace(": ", "__").replace(" ", "_").replace("/", "_") + ".log")


def stage(name: str, command: str, cwd: Path, env: dict[str, str] | None = None) -> Result:
    started = time.monotonic()
    code, output = run(command, cwd, env)
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path(name).write_text(output)
    seconds = time.monotonic() - started
    if code == 0:
        return Result(name, "PASS", seconds)
    if any(marker in output for marker in OFFLINE_MARKERS):
        return Result(name, "INCONCLUSIVE", seconds, "network unavailable")
    return Result(name, "FAIL", seconds, output.strip().splitlines()[-1][:110] if output else "")


def fe_unit() -> Result:
    result = stage("fe: unit", "pnpm test", FE)
    if result.status != "FAIL":
        return result
    placeholder = {"ALLOW_PLACEHOLDER_BE_COMMIT": "1"}
    code, _ = run("pnpm test", FE, placeholder)
    log = log_path(result.name).read_text()
    if code == 0 and "BE_COMMIT is a full commit SHA" in log:
        return Result(
            result.name,
            "BLOCKED",
            result.seconds,
            "benefit-fe/openapi/BE_COMMIT is a placeholder until benefit-be is pushed",
        )
    return result


class Services:
    """MongoDB, Temporal and the app processes, started only when a stage needs them."""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen[bytes]] = []

    def ensure_mongo(self) -> None:
        run("make mongo-up", BE)

    def ensure_temporal(self) -> None:
        code, _ = run("temporal operator cluster health", BE)
        if code != 0:
            LOGS.mkdir(parents=True, exist_ok=True)
            log = (LOGS / "temporal.log").open("wb")
            temporal = shutil.which("temporal")
            if temporal is None:
                raise RuntimeError("the temporal CLI is not installed (brew bundle)")
            command = [temporal, "server", "start-dev", "--headless"]
            command += ["--db-filename", ".data/temporal-verify.db"]
            self.processes.append(
                subprocess.Popen(command, cwd=BE, stdout=log, stderr=log)  # noqa: S603
            )
            for _ in range(30):
                time.sleep(1)
                if run("temporal operator cluster health", BE)[0] == 0:
                    return

    def start_stack(self) -> bool:
        LOGS.mkdir(parents=True, exist_ok=True)
        log = (LOGS / "stack.log").open("wb")
        env = {**os.environ, **STACK_ENV}
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("uv is not installed (brew bundle)")
        # Temporal is already running by now (started here or before), so the stack starts
        # without its own `temporal` process. Two would fight over port 7233.
        procfile = LOGS / "Procfile.stack"
        lines = (BE / "Procfile.ci").read_text().splitlines()
        procfile.write_text("\n".join(x for x in lines if not x.startswith("temporal:")) + "\n")
        command = [uv, "run", "honcho", "-f", str(procfile), "start"]
        self.processes.append(
            subprocess.Popen(command, cwd=BE, env=env, stdout=log, stderr=log)  # noqa: S603
        )
        code, _ = run("scripts/ci/wait-ready.sh 120", BE, STACK_ENV)
        return code == 0

    def stop(self) -> None:
        for process in reversed(self.processes):
            process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


def e2e_stages(services: Services) -> list[Result]:
    password = ""
    for line in (BE / ".env").read_text().splitlines():
        if line.startswith("DEMO_PASSWORD="):
            password = line.split("=", 1)[1].strip().strip('"')
    results = [stage("fe: build", "pnpm build", FE)]
    services.ensure_mongo()
    services.ensure_temporal()
    run("make reset", BE, STACK_ENV)
    if not services.start_stack():
        results.append(Result("be: stack + api tests", "FAIL", 0, "stack did not become ready"))
        results.append(Result("fe: e2e", "FAIL", 0, "stack did not become ready"))
        return results
    results.append(stage("be: stack + api tests", 'uv run pytest -m "stack or api"', BE, STACK_ENV))
    results.append(stage("fe: e2e", "pnpm e2e", FE, {"E2E_PASSWORD": password}))
    return results


def main() -> int:
    services = Services()
    results: list[Result] = []
    plain: list[tuple[str, str, Path, dict[str, str] | None]] = [
        ("be: lint", "make lint", BE, None),
        ("be: types", "make typecheck", BE, None),
        ("be: pip-audit", "uv run pip-audit", BE, None),
        (
            "be: gitleaks",
            "gitleaks detect --no-banner --config scripts/gitleaks.toml --log-opts=HEAD",
            BE,
            None,
        ),
        ("be: unit", "make test-unit", BE, None),
        ("be: workflow", "make test-workflow", BE, None),
        ("be: openapi in sync", "make openapi-check", BE, None),
        ("be: contract", "make test-contract", BE, None),
        ("fe: lint", "pnpm lint", FE, None),
        ("fe: format", "pnpm format:check", FE, None),
        ("fe: types", "pnpm typecheck", FE, None),
        ("fe: client in sync", "pnpm api:check", FE, {"BE_LOCAL": "1"}),
    ]
    try:
        for name, command, cwd, env in plain:
            results.append(stage(name, command, cwd, env))
        results.append(fe_unit())
        services.ensure_mongo()
        services.ensure_temporal()
        results.append(stage("be: integration", "make test-integration", BE))
        results.extend(e2e_stages(services))
    finally:
        services.stop()

    width = max(len(r.name) for r in results)
    for r in results:
        print(f"{r.status:<12} {r.name:<{width}}  {r.seconds:6.1f}s  {r.note}")
    failed = [r for r in results if r.status != "PASS"]
    print(f"\n{len(results) - len(failed)} of {len(results)} stages passed; logs in {LOGS}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
