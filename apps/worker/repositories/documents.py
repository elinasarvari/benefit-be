"""Model ↔ BSON helpers shared by the worker repositories."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from bson.decimal128 import Decimal128

from packages.platform.scope import Scope, ScopeKind, scoped_filter


def to_bson(value: Any) -> Any:
    """Enums to their values and plain dates to UTC midnight (BSON has no date-only type)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time(0), tzinfo=UTC)
    if isinstance(value, dict):
        return {key: to_bson(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_bson(item) for item in value]
    return value


def money_to_bson(value: Any) -> Any:
    """`to_bson` for the 003 collections: money is a plain decimal string, never a float."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: money_to_bson(item) for key, item in value.items()}
    if isinstance(value, list):
        return [money_to_bson(item) for item in value]
    return to_bson(value)


def from_bson(value: Any) -> Any:
    """Stored money (a string or a `Decimal128`) as `Decimal`; everything else as read."""
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, dict):
        return {key: from_bson(item) for key, item in value.items()}
    if isinstance(value, list):
        return [from_bson(item) for item in value]
    return value


def as_date(value: Any) -> Any:
    return value.date() if isinstance(value, datetime) else value


def case_filter(
    collection: str, household_id: UUID, case_id: UUID, query: dict[str, Any]
) -> dict[str, Any]:
    """The case's system scope (case ID) plus its household, on top of `query`."""
    scope = Scope(ScopeKind.system, case_id=case_id)
    return scoped_filter(scope, collection, {"household_id": household_id, **query})
