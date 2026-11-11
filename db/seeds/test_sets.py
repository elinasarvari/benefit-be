"""Test sets for the other features, the history files and the manifest (FR-003-13, -22, SC-003-03).

Written to `db/seeds/generated/` with the fixed seed: `testsets/` (known_answers,
pay_stub_extractions, redeterminations_due, expected_metrics), `history/explanations/*.json`,
`history/notices/*.json` and `manifest.json` (seed, generator version, a SHA-256 per file and the
document count per collection). Field sets are pinned by `tests/integration/test_seeded_dataset.py`.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from db.seeds.history import History
from db.seeds.households import SeededDataset, StubDocument, snapshot_for
from db.seeds.ids import SEED
from db.seeds.renewals import reference_answers
from packages.domain.case import DeterminationOutcome
from packages.rules import evaluate
from packages.rules.renewal import renewal_items

KNOWN_ANSWERS = Path(__file__).resolve().parent / "data" / "known_answers.json"
EXTRACTION_STUBS = 15  # FR-003-13: 10 to 20
METRIC_PLACES = Decimal("0.0001")


def _dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def _envelope(anchor: date, generator_version: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "synthetic": True,
        "seed": SEED,
        "anchor_date": anchor.isoformat(),
        "generator_version": generator_version,
        "items": items,
    }


def _stub_path(stub: StubDocument) -> str:
    return f"documents/{stub.document_id}.{'png' if stub.poor_quality else 'pdf'}"


def pay_stub_items(dataset: SeededDataset) -> list[dict[str, Any]]:
    """15 stubs of the current year across all frequencies, 2 of them poor quality."""
    stubs = [s for s in dataset.stubs() if s.coverage_year == 2026]
    poor = [s for s in stubs if s.poor_quality][:2]
    clean = [s for s in stubs if not s.poor_quality]
    chosen = list(poor)
    for frequency in sorted({s.frequency.value for s in clean}):
        chosen += [s for s in clean if s.frequency.value == frequency][
            : (EXTRACTION_STUBS - 2) // 3
        ]
    chosen += [s for s in clean if s not in chosen][: EXTRACTION_STUBS - len(chosen)]
    return [
        {
            "document_id": str(s.document_id),
            "path": _stub_path(s),
            "poor_quality": s.poor_quality,
            "expected": {
                "employee_name": s.employee_name,
                "employer": s.employer,
                "frequency": s.frequency.value,
                "pay_date": s.pay_date.isoformat(),
                "gross_pay": str(s.gross_pay),
            },
        }
        for s in sorted(chosen, key=lambda s: str(s.document_id))
    ]


def redetermination_items(dataset: SeededDataset, history: History) -> list[dict[str, Any]]:
    """Active coverage ending in the demo window. The path is automatic only when the engine finds
    the household eligible at the start date and the reference lookup agrees with held data
    (FR-007-13); a household with no reference records has no identity match, so it is asked."""
    answers = reference_answers(dataset)
    items = []
    for due in sorted(history.due, key=lambda d: str(d["coverage_period_id"])):
        start: date = due["redetermination_start"]
        year = start.year if start.year in (2025, 2026) else 2026
        result = evaluate(snapshot_for(due["household"], year, as_of_date=start), start)
        held = due["household"].years[year].members
        out_of_date = renewal_items(held, answers.get(due["household_id"], []))
        automatic = result.result is DeterminationOutcome.eligible and not out_of_date
        items.append(
            {
                "household_id": str(due["household_id"]),
                "coverage_period_id": str(due["coverage_period_id"]),
                "coverage_end": due["coverage_end"].isoformat(),
                "redetermination_start": start.isoformat(),
                "expected_path": "renew_automatically" if automatic else "ask_applicant",
                "tags": sorted(
                    due["household"].scenarios & {"renewal-automatic", "renewal-one-document"}
                ),
            }
        )
    return items


def _ratio(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return str((Decimal(numerator) / Decimal(denominator)).quantize(METRIC_PLACES, ROUND_HALF_UP))


def metric_items(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per decision month (case-clock dates, FR-008-08), worked out from the seeded cases."""
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_month[case["decided_on"].strftime("%Y-%m")].append(case)
    items = []
    for month in sorted(by_month):
        decided = by_month[month]
        days = sum((c["decided_on"] - c["received_on"]).days for c in decided)
        on_time = sum(c["decided_on"] <= c["decision_deadline"] for c in decided)
        procedural = sum(c["denial_reason"] == "missing_information" for c in decided)
        count = len(decided)
        items += [
            {
                "metric": "average_days_to_decision",
                "period": month,
                "expected_value": _ratio(days, count),
                "numerator": days,
                "denominator": count,
            },
            {
                "metric": "share_decided_within_deadline",
                "period": month,
                "expected_value": _ratio(on_time, count),
                "numerator": on_time,
                "denominator": count,
            },
            {
                "metric": "procedural_denial_rate",
                "period": month,
                "expected_value": _ratio(procedural, count),
                "numerator": procedural,
                "denominator": count,
            },
        ]
    return items


def known_answer_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = json.loads(KNOWN_ANSWERS.read_text())
    return items


def write_generated(
    dataset: SeededDataset,
    history: History,
    out_dir: Path,
    generator_version: str,
    counts: dict[str, int],
) -> dict[str, Any]:
    """Write the test sets, history files and manifest; return the manifest."""
    anchor = dataset.anchor
    sets = {
        "known_answers": known_answer_items(),
        "pay_stub_extractions": pay_stub_items(dataset),
        "redeterminations_due": redetermination_items(dataset, history),
        "expected_metrics": metric_items(
            [c for c in history.documents["cases"] if c["decided_on"] is not None]
        ),
    }
    for name, items in sets.items():
        _dump(out_dir / "testsets" / f"{name}.json", _envelope(anchor, generator_version, items))
    for determination_id, explanation in history.explanations.items():
        _dump(out_dir / "history" / "explanations" / f"{determination_id}.json", explanation)
    for (case_id, notice_type), notice in history.notices.items():
        _dump(out_dir / "history" / "notices" / f"{case_id}.{notice_type}.json", notice)
    return write_manifest(out_dir, anchor, counts, generator_version)


def write_manifest(
    out_dir: Path, anchor: date, counts: dict[str, int], generator_version: str
) -> dict[str, Any]:
    """SHA-256 of every generated file (except the manifest and READMEs), for SC-003-03."""
    hashes = {
        str(path.relative_to(out_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(out_dir.rglob("*"))
        if path.is_file() and path.name not in ("manifest.json", "README.md")
    }
    manifest = {
        "synthetic": True,
        "seed": SEED,
        "anchor_date": anchor.isoformat(),
        "generator_version": generator_version,
        "files": hashes,
        "collections": counts,
    }
    _dump(out_dir / "manifest.json", manifest)
    return manifest
