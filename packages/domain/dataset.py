from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DatasetMetadata(BaseModel):
    synthetic: Literal[True] = True
    seed: int
    generator_version: str
    generated_at: datetime
