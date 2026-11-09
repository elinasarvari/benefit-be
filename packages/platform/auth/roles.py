from __future__ import annotations

from packages.domain.auth import Principal, Role

# A supervisor can do everything a caseworker can (FR-000-05).
_IMPLIES: dict[Role, frozenset[Role]] = {
    Role.applicant: frozenset({Role.applicant}),
    Role.caseworker: frozenset({Role.caseworker}),
    Role.supervisor: frozenset({Role.supervisor, Role.caseworker}),
}


def implies(role: Role) -> frozenset[Role]:
    return _IMPLIES[role]


def has_role(principal: Principal, *allowed: Role) -> bool:
    return bool(implies(principal.role) & set(allowed))
