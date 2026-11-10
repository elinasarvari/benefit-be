from __future__ import annotations

import os
import random
import uuid
from collections.abc import Mapping
from datetime import date

SEED = 20261109
DEFAULT_ANCHOR_DATE = date(2026, 11, 10)
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://benefit-case-platform.example/seed")


def anchor_from_env(env: Mapping[str, str]) -> date:
    """The fixed "today" of the data set: every seeded date is an offset from it (ADR-003-04)."""
    raw = env.get("SEED_ANCHOR_DATE")
    return date.fromisoformat(raw) if raw else DEFAULT_ANCHOR_DATE


SEED_ANCHOR_DATE = anchor_from_env(os.environ)


def rng(stream: str) -> random.Random:
    """One generator per named stream, so adding a category never shifts the others."""
    return random.Random(f"{SEED}:{stream}")  # noqa: S311 - synthetic data, not security


def seeded_id(name: str) -> uuid.UUID:
    """Every seeded ID is a uuid5 from one namespace, so a reset recreates the same IDs."""
    return uuid.uuid5(NAMESPACE, name)


HOUSEHOLD_A = seeded_id("household-a")
HOUSEHOLD_B = seeded_id("household-b")
