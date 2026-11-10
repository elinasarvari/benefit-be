"""Names shared by the migrations, provisioning and repositories."""

from __future__ import annotations

# The audit log lives in its own database so the runtime user can be given insert and find on
# that one collection, and nothing else (MongoDB cannot exclude a collection from readWrite).
from packages.platform.audit import AUDIT_COLLECTION as AUDIT_COLLECTION  # noqa: PLC0414

AUDIT_ROLE = "auditAppend"
OWNER_USER = "benefit_owner"
APP_USER = "benefit_app"
ADMIN_DB = "admin"
