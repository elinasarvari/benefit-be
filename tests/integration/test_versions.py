from __future__ import annotations

import re
import subprocess
from pathlib import Path

from packages.platform.settings import Settings
from tests.integration.fixtures import open_client

MAKEFILE = Path(__file__).parents[2] / "Makefile"


async def test_FR_000_24_store_major_versions_match_local(
    integration_settings: Settings, owner_url: str
) -> None:
    async with open_client(owner_url) as client:
        build = await client.admin.command("buildInfo")
        hello = await client.admin.command("hello")

    assert build["version"].startswith("8.0.")
    assert hello["setName"] == "rs0"


def test_FR_000_24_temporal_cli_matches_the_pinned_version() -> None:
    pinned = re.search(r"^TEMPORAL_CLI_VERSION := (\S+)", MAKEFILE.read_text(), re.MULTILINE)
    assert pinned is not None

    out = subprocess.run(  # noqa: S603
        ["temporal", "--version"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert f"temporal version {pinned.group(1)}" in out
