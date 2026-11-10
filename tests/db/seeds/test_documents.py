from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from db.seeds.documents import write_documents
from db.seeds.households import build_dataset
from db.seeds.records import to_document
from packages.platform.synthetic import is_marked

ANCHOR = date(2026, 11, 10)


def test_FR_003_12_every_generated_document_carries_synthetic_marker(tmp_path: Path) -> None:
    paths = write_documents(build_dataset(ANCHOR), tmp_path)

    assert paths
    media = {".pdf": "application/pdf", ".png": "image/png"}
    assert all(is_marked(p.read_bytes(), media[p.suffix]) for p in paths)


def test_TC_33_regeneration_is_byte_identical(tmp_path: Path) -> None:
    dataset = build_dataset(ANCHOR)
    first = write_documents(dataset, tmp_path / "a")
    second = write_documents(build_dataset(ANCHOR), tmp_path / "b")

    assert [p.name for p in first] == [p.name for p in second]
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]


def test_TC_37_pdf_text_matches_stub_facts(tmp_path: Path) -> None:
    dataset = build_dataset(ANCHOR)
    write_documents(dataset, tmp_path)

    for stub in [s for s in dataset.stubs() if not s.poor_quality][:20]:
        text = PdfReader(tmp_path / f"{stub.document_id}.pdf").pages[0].extract_text()
        assert f"${stub.gross_pay:,.2f}" in text
        assert stub.pay_date.isoformat() in text


def test_FR_003_12_pay_stubs_cover_three_frequencies_with_10pct_poor_quality() -> None:
    stubs = build_dataset(ANCHOR).stubs()

    assert len({s.frequency for s in stubs}) == 3
    assert 0.07 <= sum(s.poor_quality for s in stubs) / len(stubs) <= 0.13


def test_FR_003_10_same_seed_gives_identical_dataset(tmp_path: Path) -> None:
    def run(out: Path) -> tuple[str, list[str]]:
        dataset = build_dataset(ANCHOR)
        docs = [
            to_document(m, "member_id")
            for h in dataset.households
            for y in h.years.values()
            for m in y.members
        ]
        texts = [
            PdfReader(p).pages[0].extract_text()
            for p in write_documents(dataset, out)
            if p.suffix == ".pdf"
        ]
        return json.dumps(docs, default=str, sort_keys=True), texts

    assert run(tmp_path / "a") == run(tmp_path / "b")
