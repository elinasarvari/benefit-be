from __future__ import annotations

import pytest

from tests.conftest import LEVEL_MARKERS


def test_every_collected_test_has_exactly_one_level_marker(request: pytest.FixtureRequest) -> None:
    offenders = []
    for item in request.session.items:
        levels = [m for m in LEVEL_MARKERS if item.get_closest_marker(m)]
        if len(levels) != 1:
            offenders.append(f"{item.nodeid}: {levels}")
    assert not offenders, "tests without exactly one level marker:\n" + "\n".join(offenders)
