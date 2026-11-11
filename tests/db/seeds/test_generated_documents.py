from __future__ import annotations

from db.seeds.generate import GENERATED_DIR
from db.seeds.reset import reset
from packages.platform.settings import Settings
from packages.platform.synthetic import is_marked

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
    ".html": "text/html",
    ".json": "application/json",  # 003: history explanations/notices, test sets, manifest
}


async def test_FR_000_14_every_generated_document_is_marked(
    integration_settings: Settings,
) -> None:
    await reset(integration_settings, analytics=False)

    files = [path for path in GENERATED_DIR.rglob("*") if path.is_file()]

    assert files, "the seed must generate at least one document"
    unmarked = [
        str(path)
        for path in files
        if not is_marked(path.read_bytes(), MEDIA_TYPES[path.suffix.lower()])
    ]
    assert unmarked == []
