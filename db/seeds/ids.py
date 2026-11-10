from __future__ import annotations

import uuid

SEED = 20261109
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://benefit-case-platform.example/seed")


def seeded_id(name: str) -> uuid.UUID:
    """Every seeded ID is a uuid5 from one namespace, so a reset recreates the same IDs."""
    return uuid.uuid5(NAMESPACE, name)


HOUSEHOLD_A = seeded_id("household-a")
HOUSEHOLD_B = seeded_id("household-b")
