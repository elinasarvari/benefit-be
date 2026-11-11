"""Turn validated domain models into MongoDB documents (the shape the 0002 validators expect).

UUIDs stay UUIDs (standard binary), `date` becomes a UTC midnight BSON date, `Decimal` becomes a
string (ADR-003-02), enums become their values. A model's identity field becomes `_id`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel


def _convert(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {name: _convert(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, dict):
        return {(k.value if isinstance(k, Enum) else k): _convert(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_convert(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    return value


def to_document(model: BaseModel, id_field: str | None = None) -> dict[str, Any]:
    document: dict[str, Any] = _convert(model)
    if id_field is not None:
        document["_id"] = document.pop(id_field)
    return document
