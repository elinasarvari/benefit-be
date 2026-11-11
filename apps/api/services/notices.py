"""A case's notices for staff (007a, FR-007-03): the held request a `notice_approval` task asks to
approve."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from apps.api.schemas.mvp import CaseNoticeList, CaseNoticeView
from apps.api.services.cases import CaseRepository
from packages.domain.auth import Principal
from packages.domain.case import workflow_id_for
from packages.platform.errors import NotFound
from packages.platform.logging import bind_context
from packages.platform.scope import Scope

MAX_NOTICES = 20


class NoticeRepository(Protocol):
    async def list_for_case(
        self, scope: Scope, case_id: UUID, limit: int
    ) -> list[CaseNoticeView]: ...


async def list_case_notices(
    case_id: UUID, principal: Principal, *, cases: CaseRepository, notices: NoticeRepository
) -> CaseNoticeList:
    bind_context(case_id=str(case_id), workflow_id=workflow_id_for(case_id))
    scope = Scope.for_principal(principal)
    if await cases.find_case(scope, case_id) is None:
        raise NotFound()
    return CaseNoticeList(items=await notices.list_for_case(scope, case_id, MAX_NOTICES))
