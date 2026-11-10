"""Write the API's OpenAPI document to openapi/openapi.json (the committed contract, FR-000-26)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from apps.api.main import create_app
from packages.platform.settings import Settings

OUTPUT = Path(__file__).resolve().parent.parent / "openapi" / "openapi.json"


def build_schema() -> dict[str, object]:
    """Build the schema from a throwaway app. It needs no running services."""
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        MONGO_URL_APP="mongodb://app:x@localhost:27017/?replicaSet=rs0",
        MONGO_URL_OWNER="mongodb://owner:x@localhost:27017/?replicaSet=rs0",
        SESSION_SECRET="s" * 40,
        SYSTEM_CREDENTIAL_SECRET="c" * 40,
    )
    return create_app(settings).openapi()


def render(schema: dict[str, object]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(build_schema()))
    print(f"wrote {OUTPUT.relative_to(OUTPUT.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
