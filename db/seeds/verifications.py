"""Seeded identity and pay stub verifications, the interim `VerifiedFacts` source (ADR-003-03).

One document per seeded member, every coverage year: identity verified, and for each declared job
the pay stubs that exist as documents. A declared job whose stub was never supplied has an empty
`stubs` list, so the engine reports it as missing (demo household C, FR-003-14). 002 and 005 later
replace this collection with real verification.
"""

from __future__ import annotations

from typing import Any

from db.seeds.households import YEARS, SeededDataset
from db.seeds.ids import seeded_id
from db.seeds.records import to_document
from packages.domain.household import JobIncome, PayStubFact, VerifiedMemberFacts


def verification_documents(dataset: SeededDataset) -> list[dict[str, Any]]:
    documents = []
    for household in dataset.households:
        for year in YEARS:
            data = household.years[year]
            stubs_by_job: dict[Any, list[PayStubFact]] = {}
            for stub in data.stubs:
                stubs_by_job.setdefault(stub.job_id, []).append(
                    PayStubFact(
                        document_id=stub.document_id,
                        pay_date=stub.pay_date,
                        frequency=stub.frequency,
                        gross_pay=stub.gross_pay,
                    )
                )
            for member in data.members:
                facts = VerifiedMemberFacts(
                    household_id=household.household_id,
                    member_id=member.member_id,
                    identity_verified=True,
                    jobs=[
                        JobIncome(job_id=job.job_id, stubs=stubs_by_job.get(job.job_id, []))
                        for job in member.jobs
                    ],
                )
                document = to_document(facts)
                document["_id"] = seeded_id(f"verification-{member.member_id}")
                document["synthetic"] = True
                documents.append(document)
    return documents
