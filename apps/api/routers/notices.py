"""011: notices, requests for information and the demo clock (FR-011-05, FR-011-09 to -11).

Contract stubs. Notices that need approval are approved through the existing review-task
actions route (`approve` on a `notice_approval` task).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from apps.api.dependencies.auth import require_roles
from apps.api.routers.contract_stubs import STUB_RESPONSES, not_built
from apps.api.schemas.mvp import (
    NoticeList,
    NoticeView,
    OpenRequestList,
    RequestResponseBody,
)
from packages.domain.auth import Principal, Role
from packages.domain.errors import ErrorEnvelope

# The tag is not a collection name, so the scope check (FR-000-19) does not mistake it for one.
router = APIRouter(tags=["applicant-notices"])

SIGNAL_RESPONSES: dict[int | str, dict[str, Any]] = {
    **STUB_RESPONSES,
    202: {"description": "Accepted and signalled to the case"},
    409: {"model": ErrorEnvelope, "description": "Not allowed in the case's current state"},
    503: {"model": ErrorEnvelope, "description": "Temporal unavailable"},
}


@router.get(
    "/me/notices",
    operation_id="listMyNotices",
    responses=STUB_RESPONSES,
    summary="Notices delivered to the applicant, newest first (FR-011-09)",
)
async def list_my_notices(
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
) -> NoticeList:
    not_built()


@router.get(
    "/me/notices/{notice_id}",
    operation_id="getMyNotice",
    responses=STUB_RESPONSES,
    summary="One delivered notice (FR-011-09)",
)
async def get_my_notice(
    notice_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
) -> NoticeView:
    not_built()


@router.get(
    "/me/requests",
    operation_id="listMyOpenRequests",
    responses=STUB_RESPONSES,
    summary="Open requests for information, with items and due dates (FR-011-10)",
)
async def list_my_open_requests(
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
) -> OpenRequestList:
    not_built()


@router.post(
    "/me/requests/{notice_id}/response",
    operation_id="respondToRequest",
    status_code=202,
    response_class=Response,
    responses=SIGNAL_RESPONSES,
    summary="Answer a request: uploaded documents and confirmed items (FR-011-03, FR-011-10)",
)
async def respond_to_request(
    notice_id: UUID,
    body: RequestResponseBody,
    principal: Annotated[Principal, Depends(require_roles(Role.applicant))],
) -> Response:
    not_built()


@router.post(
    "/cases/{case_id}/advance-clock",
    operation_id="advanceCaseClock",
    status_code=202,
    response_class=Response,
    responses=SIGNAL_RESPONSES,
    summary="Demo profile only: fire the case's next pending timer (FR-011-05, FR-011-11)",
)
async def advance_case_clock(
    case_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(Role.supervisor))],
) -> Response:
    not_built()
