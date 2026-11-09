import pytest

from packages.domain.health import CheckStatus, ReadinessCheck, ReadinessReport, ReadinessStatus


@pytest.mark.parametrize(
    ("checks", "expected"),
    [
        (
            [("mongo", CheckStatus.down, True), ("tracing", CheckStatus.ok, False)],
            ReadinessStatus.not_ready,
        ),
        (
            [("mongo", CheckStatus.ok, True), ("tracing", CheckStatus.down, False)],
            ReadinessStatus.degraded,
        ),
        (
            [("mongo", CheckStatus.ok, True), ("tracing", CheckStatus.ok, False)],
            ReadinessStatus.ready,
        ),
    ],
)
def test_readiness_report_status_derived_from_checks(
    checks: list[tuple[str, CheckStatus, bool]], expected: ReadinessStatus
) -> None:
    report = ReadinessReport.from_checks(
        "api", [ReadinessCheck(name=n, status=s, required=r) for n, s, r in checks]
    )
    assert report.status is expected
