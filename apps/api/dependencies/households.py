from __future__ import annotations

from fastapi import Request

from apps.api.dependencies.db import get_mongo, get_settings
from apps.api.repositories.households import MongoHouseholdRepository
from apps.api.repositories.notices import MongoNoticeRepository


def get_household_repository(request: Request) -> MongoHouseholdRepository:
    return MongoHouseholdRepository(get_mongo(request), get_settings(request).MONGO_DB)


def get_notice_repository(request: Request) -> MongoNoticeRepository:
    return MongoNoticeRepository(get_mongo(request), get_settings(request).MONGO_DB)
