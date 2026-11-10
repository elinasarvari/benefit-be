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

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from db.client import make_client
from db.seeds.documents import write_documents
from db.seeds.households import YEARS, build_dataset
from db.seeds.ids import SEED
from db.seeds.records import to_document
from db.seeds.rule_sets import seed_rule_sets
from db.seeds.users import SeedError, seed_users
from packages.platform.settings import Settings, load_settings
from packages.platform.synthetic import stamp_pdf

GENERATOR_VERSION = "0.2.0"
GENERATED_DIR = Path(__file__).resolve().parent / "generated"
SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)


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
        await db["households"].insert_many(
            [
                {
                    "_id": h.household_id,
                    "created_at": SEEDED_AT,
                    "address_state": h.address_state,
                    "seed_key": h.seed_key,
                }
                for h in dataset.households
            ]
        )
        await db["household_members"].insert_many(
            [
                to_document(m, "member_id")
                for h in dataset.households
                for y in YEARS
                for m in h.years[y].members
            ]
        )
        await seed_rule_sets(db)
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
