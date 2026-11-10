"""Check that the tests can actually fail (`make mutation-audit`).

For each deliberate bug below, the script edits the source, runs the tests that are supposed to
guard that behaviour, and restores the file. A test suite that still passes with the bug in place
is not guarding anything: that mutant "SURVIVED" and the run fails.

Only the files named below are touched, and each is restored with `git checkout`, so they must have
no uncommitted changes when the audit starts.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 - runs git and the test runners
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.verify import BE, FE, STACK_ENV, Services, run

BE_AUTH_TESTS = "tests/packages/platform/auth"


@dataclass(frozen=True)
class Mutant:
    requirement: str
    repo: Path
    path: str
    old: str
    new: str
    command: str


MUTANTS = [
    Mutant(
        "FR-000-07 session ends exactly at expiry",
        BE,
        "packages/platform/auth/tokens.py",
        "if clock.now() >= expires_at:",
        "if clock.now() > expires_at:",
        f"uv run pytest -q -x {BE_AUTH_TESTS}/test_tokens.py",
    ),
    Mutant(
        "FR-000-22 credential is bound to one case",
        BE,
        "packages/platform/auth/tokens.py",
        "if claims.case_id != expected_case_id:",
        "if False:",
        f"uv run pytest -q -x {BE_AUTH_TESTS}/test_tokens.py",
    ),
    Mutant(
        "FR-000-22 credential expires at step end",
        BE,
        "packages/platform/auth/tokens.py",
        "if int(clock.now().timestamp()) >= claims.exp:",
        "if False:",
        f"uv run pytest -q -x {BE_AUTH_TESTS}/test_tokens.py",
    ),
    Mutant(
        "FR-000-22 credential lifetime equals the step timeout",
        BE,
        "packages/platform/auth/tokens.py",
        "exp=int((now + ttl).timestamp()),",
        "exp=int((now + ttl * 2).timestamp()),",
        f"uv run pytest -q -x {BE_AUTH_TESTS}/test_tokens.py tests/apps/worker/activities",
    ),
    Mutant(
        "FR-000-21 wrong password is refused",
        BE,
        "packages/platform/auth/passwords.py",
        "return _hasher.verify(hashed, password)",
        "return True",
        f"uv run pytest -q -x {BE_AUTH_TESTS}/test_passwords.py tests/db/test_identity_local.py",
    ),
    Mutant(
        "FR-000-21 real identity provider checks the password",
        BE,
        "apps/api/identity_local.py",
        "return record.user if record and matches else None",
        "return record.user if record else None",
        "uv run pytest -q -x tests/db/test_identity_local.py",
    ),
    Mutant(
        "FR-000-05 role check on routes",
        BE,
        "apps/api/dependencies/auth.py",
        "if has_role(principal, *allowed):",
        "if True:",
        "uv run pytest -q -x tests/apps/api/dependencies/test_authz.py",
    ),
    Mutant(
        "FR-000-21 session cookie is HttpOnly",
        BE,
        "apps/api/routers/auth.py",
        "httponly=True,",
        "httponly=False,",
        "uv run pytest -q -x tests/apps/api/routers/test_auth.py",
    ),
    Mutant(
        "FR-000-07 default session length is 8 hours",
        BE,
        "packages/platform/settings.py",
        "SESSION_TTL_HOURS: int = Field(default=8,",
        "SESSION_TTL_HOURS: int = Field(default=12,",
        "uv run pytest -q -x tests/packages/platform/test_settings.py",
    ),
    Mutant(
        "FR-000-19 applicants see only their household",
        BE,
        "packages/platform/scope.py",
        "limit = _own_household(scope) if scope.role is Role.applicant else {}",
        "limit = {}",
        "uv run pytest -q -x tests/packages/platform/test_scope_filter.py tests/db/test_scope.py",
    ),
    Mutant(
        "FR-000-19 a user scope without a role is refused",
        BE,
        "packages/platform/scope.py",
        "elif scope.role is None:",
        "elif False:",
        "uv run pytest -q -x tests/packages/platform/test_scope_filter.py",
    ),
    Mutant(
        "FR-000-23 audit log has no free-text field",
        BE,
        "db/migrations/versions/0001_baseline.py",
        '"additionalProperties": False,  # no free-text or nested field can be added',
        '"additionalProperties": True,',
        "uv run pytest -q -x tests/db/test_audit_log.py",
    ),
    Mutant(
        "FR-000-11 personal data is masked in logs",
        BE,
        "packages/platform/logging.py",
        "return MASK",
        "return value",
        "uv run pytest -q -x tests/packages/platform/test_logging.py",
    ),
    Mutant(
        "FR-000-10 request log shows the route template, not the path",
        BE,
        "apps/api/middleware/request_context.py",
        'getattr(route, "path", "unmatched")',
        'scope["path"]',
        "uv run pytest -q -x tests/apps/api/middleware",
    ),
    Mutant(
        "FR-000-08 every response carries the reference ID",
        BE,
        "apps/api/middleware/request_context.py",
        "MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id",
        "pass",
        "uv run pytest -q -x tests/apps/api/test_errors.py tests/apps/api/middleware",
    ),
    Mutant(
        "FR-000-09 a required dependency down means not ready",
        BE,
        "packages/domain/health.py",
        "if any(c.required and c.status is CheckStatus.down for c in checks):",
        "if any(c.status is CheckStatus.down for c in checks):",
        "uv run pytest -q -x tests/packages/domain/test_health.py",
    ),
    Mutant(
        "FR-000-13 retries are bounded",
        BE,
        "packages/platform/resilience.py",
        "range(1, max_attempts + 1)",
        "range(1, max_attempts + 2)",
        "uv run pytest -q -x tests/packages/platform/test_resilience.py",
    ),
    Mutant(
        "FR-000-14 documents carry a visible SYNTHETIC label",
        BE,
        "packages/platform/synthetic.py",
        "page.merge_page(overlay.pages[0])",
        "pass",
        "uv run pytest -q -x tests/packages/platform/test_synthetic.py",
    ),
    Mutant(
        "FR-000-27 dates use America/Chicago",
        BE,
        "packages/platform/clock.py",
        'CHICAGO = ZoneInfo("America/Chicago")',
        'CHICAGO = ZoneInfo("UTC")',
        "uv run pytest -q -x tests/packages/platform/test_clock.py "
        "tests/apps/worker/workflows/test_calendar.py",
    ),
    Mutant(
        "FR-000-05 a supervisor counts as a caseworker (UI)",
        FE,
        "src/lib/roles.ts",
        "supervisor: ['supervisor', 'caseworker'],",
        "supervisor: ['supervisor'],",
        "pnpm exec vitest run",
    ),
    Mutant(
        "FR-000-27 UI dates use America/Chicago",
        FE,
        "src/lib/time.ts",
        "const TIME_ZONE = 'America/Chicago'",
        "const TIME_ZONE = 'UTC'",
        "pnpm exec vitest run src/lib",
    ),
    Mutant(
        "FR-000-05 each role lands in its own area (UI)",
        FE,
        "src/lib/roles.ts",
        "role === 'applicant' ? '/applicant' : '/caseworker'",
        "role === 'applicant' ? '/caseworker' : '/applicant'",
        "pnpm exec vitest run",
    ),
]


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("git") or "git"
    return subprocess.run(  # noqa: S603
        [executable, *args], cwd=repo, capture_output=True, text=True, check=False
    )


def audit(mutant: Mutant) -> tuple[str, str]:
    target = mutant.repo / mutant.path
    if git(mutant.repo, "diff", "--quiet", "--", mutant.path).returncode != 0:
        return "SKIPPED", "file has uncommitted changes"
    source = target.read_text()
    if source.count(mutant.old) != 1:
        return "BROKEN", "the text to mutate was not found exactly once (update the audit)"
    target.write_text(source.replace(mutant.old, mutant.new))
    try:
        env = {"ALLOW_PLACEHOLDER_BE_COMMIT": "1", **STACK_ENV}
        code, output = run(mutant.command, mutant.repo, env)
    finally:
        git(mutant.repo, "checkout", "--", mutant.path)
    if code == 0:
        return "SURVIVED", "the tests still pass with this bug in place"
    if "error" in output.lower() and "failed" not in output.lower() and " passed" not in output:
        return "ERRORED", "the tests did not run (check the command)"
    return "KILLED", ""


def main() -> int:
    services = Services()
    rows: list[tuple[str, str, str]] = []
    try:
        services.ensure_mongo()
        services.ensure_temporal()
        for mutant in MUTANTS:
            status, note = audit(mutant)
            rows.append((status, mutant.requirement, note))
            print(f"{status:<9} {mutant.requirement}  {note}", flush=True)
    finally:
        services.stop()
    bad = [r for r in rows if r[0] != "KILLED"]
    print(f"\n{len(rows) - len(bad)} of {len(rows)} deliberate bugs were caught by the tests")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
