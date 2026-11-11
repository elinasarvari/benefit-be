"""Fixed-seed synthetic data generator (BA-11, KD-13). Every value here is made up.

    python -m db.seeds.generate

Writes to MongoDB and to `db/seeds/generated/` (git-ignored). Running it twice on an empty database
gives the same IDs, because every ID is a uuid5 from the fixed namespace.
"""

from __future__ import annotations

import asyncio
import io
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from db.client import make_client
from db.seeds.documents import write_documents
from db.seeds.households import YEARS, SeededDataset, build_dataset
from db.seeds.ids import HOUSEHOLD_A, HOUSEHOLD_B, HOUSEHOLD_C, SEED
from db.seeds.records import to_document
from db.seeds.rule_sets import seed_rule_sets
from db.seeds.users import SeedError, seed_users
from packages.domain.case import DeterminationOutcome
from packages.platform.settings import Settings, load_settings
from packages.platform.synthetic import stamp_pdf

GENERATOR_VERSION = "0.2.0"
GENERATED_DIR = Path(__file__).resolve().parent / "generated"
SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)

# The result the 001 verification and determination stubs return for each household (research
# R1), so `make dev` can drive every path: applicant1 approved, applicant2 review then denial or
# override, applicant3 cannot-determine. 002 and 003 replace the stubs and ignore this field.
# A, B and C are three of the 200 generated households (003, ADR-003-07); until 003's
# determination activity replaces the stub (T023), the field stays on them (T011 drops it).
STUB_RESULTS: dict[UUID, DeterminationOutcome] = {
    HOUSEHOLD_A: DeterminationOutcome.eligible,
    HOUSEHOLD_B: DeterminationOutcome.ineligible,
    HOUSEHOLD_C: DeterminationOutcome.cannot_determine,
}


def household_documents(dataset: SeededDataset) -> list[dict[str, Any]]:
    """One `households` document per generated household; the demo ones carry their stub result."""
    documents: list[dict[str, Any]] = []
    for h in dataset.households:
        doc: dict[str, Any] = {
            "_id": h.household_id,
            "created_at": SEEDED_AT,
            "address_state": h.address_state,
            "seed_key": h.seed_key,
        }
        if h.household_id in STUB_RESULTS:
            doc["stub_result"] = STUB_RESULTS[h.household_id].value
        documents.append(doc)
    return documents


def stub_result_documents() -> list[dict[str, Any]]:
    """The same results keyed by `household_id`, for the worker's household scope (ADR-001-06)."""
    return [
        {"_id": hid, "household_id": hid, "created_at": SEEDED_AT, "stub_result": result.value}
        for hid, result in STUB_RESULTS.items()
    ]


def application_documents(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One synthetic `applications` document per case (ADR-001-08, PR-3): `_id` = `case_id`."""
    return [
        {
            "_id": case["case_id"],
            "case_id": case["case_id"],
            "household_id": case["household_id"],
            "submission_reference": case["submission_reference"],
            "form": {
                "members": [
                    {
                        "full_name": "SYNTH-NAME",
                        "date_of_birth": datetime(1985, 6, 15, tzinfo=UTC),
                        "relationship": "self",
                        "citizenship_status": "us_citizen",
                        "medicare_entitled": False,
                        "zero_income": True,
                        "jobs": [],
                    }
                ],
                "address": {
                    "line1": "1 Synthetic Way",
                    "line2": None,
                    "city": "Springfield",
                    "state": "IL",
                    "postal_code": "62701",
                },
            },
            "submitted_at": case["clock"]["case_start"],
            "synthetic": True,
            "created_at": SEEDED_AT,
            "updated_at": SEEDED_AT,
        }
        for case in cases
    ]


def sample_pay_stub() -> bytes:
    """A one-page synthetic pay stub. Content sits well inside the label bands."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(72, 700, "Sample Employer Inc. (synthetic)")
    pdf.setFont("Helvetica", 11)
    for offset, line in enumerate(
        [
            "Employee: Sample Worker",
            "Pay frequency: bi-weekly",
            "Pay period ending: 2026-01-15",
            "Gross pay: $1,200.00",
        ]
    ):
        pdf.drawString(72, 670 - 20 * offset, line)
    pdf.showPage()
    pdf.save()
    return stamp_pdf(buffer.getvalue())


async def seed(settings: Settings) -> None:
    password = settings.DEMO_PASSWORD.get_secret_value()
    if not password:  # refuse before writing anything
        raise SeedError("DEMO_PASSWORD is not set; run make setup or set it in .env")
    GENERATED_DIR.joinpath("documents").mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.joinpath("documents", "sample_pay_stub.pdf").write_bytes(sample_pay_stub())

    client = make_client(settings.MONGO_URL_OWNER.get_secret_value())
    try:
        db = client[settings.MONGO_DB]
        dataset = build_dataset(settings.SEED_ANCHOR_DATE)
        write_documents(dataset, GENERATED_DIR / "documents")
        await db["households"].insert_many(household_documents(dataset))
        await db["household_stub_results"].insert_many(stub_result_documents())
        await db["household_members"].insert_many(
            [
                to_document(m, "member_id")
                for h in dataset.households
                for y in YEARS
                for m in h.years[y].members
            ]
        )
        await seed_rule_sets(db)
        # No case is seeded today; once one is, it gets its application here.
        seeded_cases = [doc async for doc in db["cases"].find()]
        if seeded_cases:
            await db["applications"].insert_many(application_documents(seeded_cases))
        await seed_users(db, password, SEEDED_AT)
        await db["_meta"].replace_one(
            {"_id": "dataset"},
            {
                "_id": "dataset",
                "synthetic": True,
                "seed": SEED,
                "generator_version": GENERATOR_VERSION,
                "generated_at": SEEDED_AT,
                "anchor_date": datetime.combine(
                    settings.SEED_ANCHOR_DATE, datetime.min.time(), tzinfo=UTC
                ),
            },
            upsert=True,
        )
    finally:
        await client.close()


def main() -> int:
    try:
        asyncio.run(seed(load_settings()))
    except SeedError as exc:
        print(f"seed refused: {exc}", file=sys.stderr)
        return 2
    print(f"seeded synthetic data (seed {SEED})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
