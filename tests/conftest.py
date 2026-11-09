from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from packages.platform.settings import Settings

LEVEL_MARKERS = ("unit", "workflow", "contract", "integration", "api", "stack")
TESTS_ROOT = Path(__file__).parent

TEST_ENV = {
    "APP_ENV": "test",
    "LLM_MODE": "fake",
    "EMBEDDER": "fake",
    "RERANKER": "fake",
    "TRACING_ENABLED": "false",
}


SECRET_ENV = (
    "SESSION_SECRET",
    "SYSTEM_CREDENTIAL_SECRET",
    "DEMO_PASSWORD",
    "MONGO_URL_APP",
    "MONGO_URL_OWNER",
)


def level_for(relative: Path) -> str:
    """Level marker implied by a test file's location, so no test needs a decorator."""
    parts = relative.parts
    name = relative.name
    if parts[0] == "contract":
        return "contract"
    if parts[0] == "api":
        return "api"
    if parts[0] == "stack":
        return "stack"
    if parts[0] in ("integration", "db"):
        return "integration"
    if "workflows" in parts or (
        parts[:3] == ("apps", "worker", name) and name.startswith("test_smoke")
    ):
        return "workflow"
    return "unit"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if any(item.get_closest_marker(m) for m in LEVEL_MARKERS):
            continue
        path = Path(str(item.path))
        if TESTS_ROOT not in path.parents:
            continue
        item.add_marker(getattr(pytest.mark, level_for(path.relative_to(TESTS_ROOT))))


@pytest.fixture(autouse=True)
def _test_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    # Importing dbt loads .env into os.environ. Tests pass secrets explicitly or via Settings.
    for key in SECRET_ENV:
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        MONGO_URL_APP="mongodb://app:pw@localhost:27017/?replicaSet=rs0&authSource=admin",  # type: ignore[arg-type]
        MONGO_URL_OWNER="mongodb://owner:pw@localhost:27017/?replicaSet=rs0&authSource=admin",  # type: ignore[arg-type]
        SESSION_SECRET="s" * 40,  # type: ignore[arg-type]
        SYSTEM_CREDENTIAL_SECRET="c" * 40,  # type: ignore[arg-type]
    )
