from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class DatasetMetadata(BaseModel):
    synthetic: Literal[True] = True
    seed: int
    anchor_date: date
    generator_version: str
    generated_at: datetime
