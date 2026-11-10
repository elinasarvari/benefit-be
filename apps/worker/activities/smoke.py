from __future__ import annotations

import structlog
from temporalio import activity

from packages.domain.smoke import SmokeInput

log = structlog.get_logger()


@activity.defn(name="smoke_step")
async def smoke_step(data: SmokeInput) -> str:
    log.info("smoke.step", case_id=str(data.case_id))
    return f"smoke ok for {data.case_id}"
