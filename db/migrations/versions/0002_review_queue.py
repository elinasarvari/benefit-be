"""Review queue (feature 010): full validators and queue indexes for `review_tasks` and
`caseworker_actions`, replacing the baseline minimal validators.

Enum values are frozen here from `packages/domain/review.py` as of this migration: a later change
to those enums goes in a later migration, never into this file (KD-11).
"""

from __future__ import annotations

from typing import Any

from db.migrate import MigrationContext

UUID = {"bsonType": "binData"}
NULLABLE_UUID = {"bsonType": ["binData", "null"]}
DATE = {"bsonType": "date"}
NULLABLE_DATE = {"bsonType": ["date", "null"]}

ROLES = ["applicant", "caseworker", "supervisor"]
REVIEW_REASONS = [
    "adverse_determination",
    "cannot_determine",
    "notice_approval",
    "verification_review",
    "missed_response_deadline",
    "explanation_review",
]
ACTION_TYPES = [
    "approve_denial",
    "override_to_approve",
    "request_information",
    "correct_value",
    "dismiss_finding",
    "accept_findings",
    "approve",
    "edit_and_approve",
    "deny_missing_information",
    "extend_15_days",
    "continue_with_held_data",
]
REVIEW_CAUSES = [
    "low_confidence",
    "unreadable_document",
    "different_person",
    "identity_mismatch",
    "income_source_mismatch",
    "missing_citation",
    "verification_unavailable",
    "retry_limit_reached",
    "non_retryable_error",
    "determination_failed",
    "ineligible",
    "cannot_determine",
    "notice_ready",
    "response_deadline_missed",
    "explanation_check_failed",
]
TASK_STATUSES = ["open", "closed"]
REVIEW_FLAGS = ["reminder", "escalated"]
ACTION_OUTCOMES = ["applied", "rejected"]
REJECTION_CODES = [
    "not_allowed_for_reason",
    "reason_required",
    "task_closed",
    "extension_already_used",
    "missing_required_field",
    "request_draft_failed",
]


def _string(max_length: int = 128) -> dict[str, Any]:
    return {"bsonType": "string", "minLength": 1, "maxLength": max_length}


# A pointer to evidence: a kind and an ID only, never the content (R3).
EVIDENCE_REF: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["kind", "id"],
    "properties": {"kind": _string(64), "id": UUID},
}

REVIEW_TASKS_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": [
        "_id",
        "household_id",
        "case_id",
        "workflow_id",
        "reason",
        "cause",
        "evidence_refs",
        "blocking",
        "status",
        "opened_at_case",
        "opened_at_real",
        "flags",
        "decision_deadline",
        "created_at",
        "updated_at",
    ],
    "properties": {
        "_id": UUID,
        "task_id": UUID,
        "household_id": UUID,
        "case_id": UUID,
        "workflow_id": _string(256),
        "reason": {"enum": REVIEW_REASONS},
        "cause": {"enum": REVIEW_CAUSES},
        "evidence_refs": {"bsonType": "array", "maxItems": 50, "items": EVIDENCE_REF},
        "blocking": {"bsonType": "bool"},
        "status": {"enum": TASK_STATUSES},
        "opened_at_case": DATE,
        "opened_at_real": DATE,
        "reminder_at": NULLABLE_DATE,
        "escalated_at": NULLABLE_DATE,
        "flags": {
            "bsonType": "array",
            "uniqueItems": True,
            "items": {"enum": REVIEW_FLAGS},
        },
        # Calendar dates are stored as midnight-UTC BSON dates (database.md).
        "decision_deadline": DATE,
        "coverage_end": NULLABLE_DATE,
        "closed_by_action_id": NULLABLE_UUID,
        "created_at": DATE,
        "updated_at": DATE,
    },
}

CORRECTED_VALUE: dict[str, Any] = {
    "bsonType": ["object", "null"],
    "additionalProperties": False,
    "required": ["field_ref", "value"],
    "properties": {
        "field_ref": _string(256),
        "value": {"bsonType": ["string", "int", "long", "double", "decimal", "bool", "null"]},
    },
}

CASEWORKER_ACTIONS_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": [
        "_id",
        "action_id",
        "task_id",
        "household_id",
        "case_id",
        "action",
        "reviewer_id",
        "reviewer_role",
        "submitted_at",
        "outcome",
        "recorded_at",
    ],
    "properties": {
        "_id": {"bsonType": ["binData", "objectId"]},
        "action_id": UUID,
        "task_id": UUID,
        "household_id": UUID,
        "case_id": UUID,
        "action": {"enum": ACTION_TYPES},
        "reviewer_id": UUID,
        "reviewer_role": {"enum": ROLES},
        # Free text written by staff (PL-11): bounded, never logged.
        "reason": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 500},
        "corrected_value": CORRECTED_VALUE,
        "finding_id": NULLABLE_UUID,
        "revision_id": NULLABLE_UUID,
        "submitted_at": DATE,
        "outcome": {"enum": ACTION_OUTCOMES},
        "rejection_code": {"enum": [*REJECTION_CODES, None]},
        "applied_at_case": NULLABLE_DATE,
        "recorded_at": DATE,
    },
}

# The baseline minimal validator for collections scoped to a case (0001_baseline), restored on
# downgrade. Copied, not imported, so this migration stays frozen (KD-11).
BASELINE_SCOPED_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["_id", "household_id", "case_id"],
    "properties": {"household_id": UUID, "case_id": UUID},
}

# Equality, then sort (database.md).
REVIEW_TASK_INDEXES = [
    ([("status", 1), ("decision_deadline", 1)], "status_deadline", False),
    ([("status", 1), ("flags", 1), ("decision_deadline", 1)], "status_flags_deadline", False),
]
CASEWORKER_ACTION_INDEXES = [
    ([("action_id", 1)], "action_id_unique", True),
    ([("task_id", 1), ("recorded_at", 1)], "task_recorded", False),
]

COLLECTIONS: list[tuple[str, dict[str, Any], list[tuple[list[tuple[str, int]], str, bool]]]] = [
    ("review_tasks", REVIEW_TASKS_SCHEMA, REVIEW_TASK_INDEXES),
    ("caseworker_actions", CASEWORKER_ACTIONS_SCHEMA, CASEWORKER_ACTION_INDEXES),
]


async def _set_validator(ctx: MigrationContext, name: str, schema: dict[str, Any]) -> None:
    await ctx.db.command(
        "collMod",
        name,
        validator={"$jsonSchema": schema},
        validationLevel="strict",
        validationAction="error",
    )


async def upgrade(ctx: MigrationContext) -> None:
    for name, schema, indexes in COLLECTIONS:
        await _set_validator(ctx, name, schema)
        for keys, index_name, unique in indexes:
            await ctx.db[name].create_index(keys, name=index_name, unique=unique)


async def downgrade(ctx: MigrationContext) -> None:
    for name, _schema, indexes in COLLECTIONS:
        for _keys, index_name, _unique in indexes:
            await ctx.db[name].drop_index(index_name)
        await _set_validator(ctx, name, BASELINE_SCOPED_SCHEMA)
