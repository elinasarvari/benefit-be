"""Synthetic application forms for the API tests (FR-001-24). Names and employers are fake."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from packages.domain.case import ApplicationForm, ApplicationSubmission

SYNTH_NAME = "SYNTH-NAME"
SYNTH_EMPLOYER = "SYNTH-EMPLOYER"


def form_dict() -> dict[str, Any]:
    return {
        "members": [
            {
                "full_name": SYNTH_NAME,
                "date_of_birth": "1985-04-12",
                "relationship": "self",
                "citizenship_status": "us_citizen",
                "medicare_entitled": False,
                "zero_income": False,
                "jobs": [
                    {
                        "employer": SYNTH_EMPLOYER,
                        "pay_frequency": "biweekly",
                        "declared_monthly_gross": "1800.00",
                    }
                ],
            },
            {
                "full_name": "SYNTH-SPOUSE",
                "date_of_birth": "1987-09-30",
                "relationship": "spouse",
                "citizenship_status": "qualifying_immigrant",
                "medicare_entitled": False,
                "zero_income": True,
                "jobs": [],
            },
        ],
        "address": {
            "line1": "1 Synthetic Way",
            "line2": None,
            "city": "Springfield",
            "state": "IL",
            "postal_code": "62701",
        },
    }


def form() -> ApplicationForm:
    return ApplicationForm.model_validate(form_dict())


def body_dict(reference: UUID | str) -> dict[str, Any]:
    return {"submission_reference": str(reference), "form": form_dict()}


def submission(reference: UUID) -> ApplicationSubmission:
    return ApplicationSubmission(submission_reference=reference, form=form())
