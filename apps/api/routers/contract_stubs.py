"""Shared pieces of the MVP contract stubs (specs/mvp-contracts.md section 3).

Every stubbed endpoint has its real path, roles, parameters and bodies, and answers 501 until
its feature replaces the body. Delete this module once no router imports it.
"""

from __future__ import annotations

from typing import Any, NoReturn

from packages.domain.errors import ErrorEnvelope
from packages.platform.errors import NotBuiltYet

STUB_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope, "description": "Not signed in"},
    403: {"model": ErrorEnvelope, "description": "Role not allowed"},
    404: {"model": ErrorEnvelope, "description": "Not found or not visible to the caller"},
    422: {"model": ErrorEnvelope, "description": "validation_failed"},
    501: {"model": ErrorEnvelope, "description": "not_implemented: the feature has not landed"},
}


def not_built() -> NoReturn:
    raise NotBuiltYet()
