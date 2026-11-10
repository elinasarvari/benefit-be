from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from packages.domain.dataset import DatasetMetadata


def test_FR_003_10_dataset_metadata_requires_anchor_date_and_synthetic_true() -> None:
    fields = {
        "seed": 42,
        "generator_version": "1",
        "generated_at": datetime(2026, 11, 10, tzinfo=UTC),
    }

    with pytest.raises(ValidationError):
        DatasetMetadata(**fields)
    with pytest.raises(ValidationError):
        DatasetMetadata(**fields, anchor_date=date(2026, 11, 10), synthetic=False)

    meta = DatasetMetadata(**fields, anchor_date=date(2026, 11, 10))

    assert meta.synthetic is True
    assert meta.model_dump(mode="json")["anchor_date"] == "2026-11-10"
