# Contract: CaseWorkflow (Workflows provides; Platform & UI, 010, 011 use)

Models are in `packages/domain/case.py` (see `../data-model.md`). Merged in the contracts step before lanes start (constitution VI, BA-8).

## Workflow

- **Type:** `CaseWorkflow` (TW-3, TW-6). **ID:** `case-{case_id}` (matches `apps/worker/workflows/smoke.py:24`). **Task queue:** `TEMPORAL_TASK_QUEUE`.
- **Input:** `CaseStart`. **Result:** `CaseStatus` (final).
- Started only by the API after the case document is inserted (ADR-001-02). An already-started error on start is success.

## Signals (TW-5; fallback of ADR-001-04)

| Name | Payload | Effect |
|---|---|---|
| `caseworker_action` | `CaseworkerActionRequest` | Applies the action to the open task if allowed, else records `action_rejected`. Duplicate `request_id` is ignored. |

Reserved for later features (names fixed now): `applicant_response` (011), `advance_clock` (011, demo profile only).

## Queries

| Name | Result |
|---|---|
| `status` | `CaseStatus` (computed with `case_now(workflow.now())`) |
| `allowed_actions` | `AllowedActions {task_id, actions: list[CaseworkerActionType]}` or empty |

## Activities (names are the contract; TW-2, TW-9, TW-10)

| Name | Input → Output | Options (`apps/worker/policies.py`) | Owner of the body |
|---|---|---|---|
| `record_case_change` | `CaseChange` → `None` | `UNTIL_DONE` | Workflows (calls `apps/worker/repositories/cases.py`, per `database.md` scope line) |
| `verify_documents` | `VerificationRequest {case_id, household_id}` → `VerificationOutcome {complete: bool, needs_review: bool}` | `AGENT_STEP` | stub in 001; 002 replaces |
| `determine_eligibility` | `DeterminationRequest {case_id, household_id, as_of_date}` → `Determination` (minimal) | `UNTIL_DONE` | stub in 001; 003 replaces |

`UNTIL_DONE` retries without limit on the server (exponential backoff capped at 5 minutes, a 30-second timeout per attempt, `NON_RETRYABLE_ERRORS` not retried), so an outage neither fails the case nor grows its history; a workflow cancel cancels the activity. All activities are idempotent (TW-11): `record_case_change` by `(case_id, seq)` and deterministic `_id`s; the stubs are pure.
