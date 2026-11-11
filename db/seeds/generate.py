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

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from db.client import make_client
from db.seeds.documents import write_documents
from db.seeds.history import History, build_history
from db.seeds.households import YEARS, SeededDataset, build_dataset
from db.seeds.ids import SEED
from db.seeds.records import to_document
from db.seeds.renewals import reference_record_documents
from db.seeds.rule_sets import rule_set_documents, seed_rule_sets
from db.seeds.test_sets import write_generated
from db.seeds.users import DEMO_ACCOUNTS, SeedError, seed_users
from db.seeds.verifications import verification_documents
from packages.platform.settings import Settings, load_settings
from packages.platform.synthetic import stamp_pdf

GENERATOR_VERSION = "0.4.0"
GENERATED_DIR = Path(__file__).resolve().parent / "generated"
SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def household_documents(dataset: SeededDataset) -> list[dict[str, Any]]:
    """One `households` document per generated household."""
    return [
        {
            "_id": h.household_id,
            "created_at": SEEDED_AT,
            "address_state": h.address_state,
            "seed_key": h.seed_key,
            "synthetic": True,
        }
        for h in dataset.households
    ]


def member_documents(dataset: SeededDataset) -> list[dict[str, Any]]:
    documents = []
    for h in dataset.households:
        for y in YEARS:
            for m in h.years[y].members:
                document = to_document(m, "member_id")
                document["synthetic"] = True
                documents.append(document)
    return documents


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
    pdf = canvas.Canvas(buffer, pagesize=letter, invariant=1)
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


def collection_counts(dataset: SeededDataset, history: History) -> dict[str, int]:
    """What `seed` writes to each collection, for the manifest (FR-003-22)."""
    return {
        "households": len(dataset.households),
        "household_members": sum(
            len(h.years[y].members) for h in dataset.households for y in YEARS
        ),
        "seeded_verifications": len(verification_documents(dataset)),
        "reference_records": len(reference_record_documents(dataset)),
        "rule_sets": len(rule_set_documents()),
        "users": len(DEMO_ACCOUNTS),
        "applications": len(history.documents["cases"]),
        **history.counts(),
    }


def generate_files(dataset: SeededDataset, history: History, out_dir: Path) -> dict[str, Any]:
    """Write every generated file (documents, test sets, history, manifest); no database."""
    out_dir.joinpath("documents").mkdir(parents=True, exist_ok=True)
    out_dir.joinpath("documents", "sample_pay_stub.pdf").write_bytes(sample_pay_stub())
    write_documents(dataset, out_dir / "documents")
    return write_generated(
        dataset, history, out_dir, GENERATOR_VERSION, collection_counts(dataset, history)
    )


async def seed(settings: Settings) -> None:
    password = settings.DEMO_PASSWORD.get_secret_value()
    if not password:  # refuse before writing anything
        raise SeedError("DEMO_PASSWORD is not set; run make setup or set it in .env")
    dataset = build_dataset(settings.SEED_ANCHOR_DATE)
    history = build_history(dataset)
    generate_files(dataset, history, GENERATED_DIR)

    client = make_client(settings.MONGO_URL_OWNER.get_secret_value())
    try:
        db = client[settings.MONGO_DB]
        await db["households"].insert_many(household_documents(dataset))
        await db["household_members"].insert_many(member_documents(dataset))
        await db["seeded_verifications"].insert_many(verification_documents(dataset))
        await db["reference_records"].insert_many(reference_record_documents(dataset))
        await seed_rule_sets(db)
        # Completed history goes into an empty `cases` collection only, so seeding on top of
        # a case inserted by hand never mixes the two.
        if await db["cases"].count_documents({}, limit=1) == 0:
            for name, documents in history.documents.items():
                if documents:
                    await db[name].insert_many(documents)
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
