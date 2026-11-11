"""Scoped reads of `decision_explanations` and the history over `cases`, `determinations`,
`coverage_periods` (FR-004-15, FR-004-16; research R9).

Every read goes through `packages.platform.scope` (PL-6) and is bounded: at most 20 explanations
per case and 50 cases per history (database.md, no unbounded find). The API never builds an
explanation; it returns what was stored (PL-2, FR-004-13).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from apps.api.repositories.cases import (
    CASE_FIELDS,
    COVERAGE_FIELDS,
    _case_from_bson,
    _coverage_from_bson,
)
from packages.domain.case import Case, CoveragePeriod
from packages.domain.explanation import DecisionExplanation, ExplanationAudience
from packages.platform.mongo import Client
from packages.platform.scope import Scope, scoped_filter

DECISION_EXPLANATIONS = "decision_explanations"
DETERMINATIONS = "determinations"
CASES = "cases"
COVERAGE_PERIODS = "coverage_periods"
MAX_EXPLANATIONS = 20
MAX_HISTORY = 50
EXPLANATION_FIELDS = {name: 1 for name in DecisionExplanation.model_fields}


@dataclass(frozen=True)
class HistoryRow:
    case: Case
    coverage: CoveragePeriod | None
    explanation: DecisionExplanation | None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def explanation_from_bson(doc: dict[str, Any]) -> DecisionExplanation:
    """Stored dates are BSON dates; the citation's manual version is a calendar date."""
    data = {key: value for key, value in doc.items() if key != "_id"}
    data["built_at"] = _utc(data["built_at"])
    citations = []
    for citation in data.get("citations", []):
        version = citation["manual_version"]
        if isinstance(version, datetime):
            version = version.astimezone(UTC).date()
        citations.append({**citation, "manual_version": version})
    data["citations"] = citations
    return DecisionExplanation.model_validate(data)


class MongoExplanationRepository:
    def __init__(self, client: Client, db_name: str) -> None:
        self._client = client
        self._db_name = db_name

    async def list_for_case(self, scope: Scope, case_id: UUID) -> list[DecisionExplanation]:
        """The case's caseworker explanations, newest first, at most 20."""
        query = {"case_id": case_id, "audience": ExplanationAudience.caseworker.value}
        cursor = (
            self._client[self._db_name][DECISION_EXPLANATIONS]
            .find(scoped_filter(scope, DECISION_EXPLANATIONS, query), EXPLANATION_FIELDS)
            .sort([("built_at", -1), ("_id", 1)])
            .limit(MAX_EXPLANATIONS)
        )
        return [explanation_from_bson(doc) for doc in await cursor.to_list(length=MAX_EXPLANATIONS)]

    async def history(self, scope: Scope, household_id: UUID) -> list[HistoryRow]:
        """The household's cases, newest `received_on` first, at most 50, each with the applicant
        explanation of its latest determination (None when it has none)."""
        db = self._client[self._db_name]
        case_cursor = (
            db[CASES]
            .find(scoped_filter(scope, CASES, {"household_id": household_id}), CASE_FIELDS)
            .sort([("received_on", -1), ("_id", 1)])
            .limit(MAX_HISTORY)
        )
        cases = [_case_from_bson(doc) for doc in await case_cursor.to_list(length=MAX_HISTORY)]
        if not cases:
            return []
        case_ids = [case.case_id for case in cases]

        coverage_cursor = db[COVERAGE_PERIODS].find(
            scoped_filter(scope, COVERAGE_PERIODS, {"case_id": {"$in": case_ids}}),
            COVERAGE_FIELDS,
        )
        coverage = {
            period.case_id: period
            for period in [
                _coverage_from_bson(doc) for doc in await coverage_cursor.to_list(MAX_HISTORY)
            ]
        }

        latest_pipeline = [
            {"$match": scoped_filter(scope, DETERMINATIONS, {"case_id": {"$in": case_ids}})},
            {"$sort": {"recorded_at": -1, "_id": 1}},
            {"$group": {"_id": "$case_id", "determination_id": {"$first": "$_id"}}},
        ]
        latest_cursor = await db[DETERMINATIONS].aggregate(latest_pipeline)
        latest = {
            doc["_id"]: doc["determination_id"] for doc in await latest_cursor.to_list(MAX_HISTORY)
        }

        explanation_cursor = db[DECISION_EXPLANATIONS].find(
            scoped_filter(
                scope,
                DECISION_EXPLANATIONS,
                {
                    "case_id": {"$in": case_ids},
                    "determination_id": {"$in": list(latest.values())},
                    "audience": ExplanationAudience.applicant.value,
                },
            ),
            EXPLANATION_FIELDS,
        )
        explanations = {
            doc["determination_id"]: explanation_from_bson(doc)
            for doc in await explanation_cursor.to_list(MAX_HISTORY)
        }
        rows: list[HistoryRow] = []
        for case in cases:
            determination_id = latest.get(case.case_id)
            rows.append(
                HistoryRow(
                    case=case,
                    coverage=coverage.get(case.case_id),
                    explanation=explanations.get(determination_id) if determination_id else None,
                )
            )
        return rows
