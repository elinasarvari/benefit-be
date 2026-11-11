"""Every seeded determination carries a complete six-rule trace (003 [qa]; TC-131; SC-003-02, -04).

Written test-first: it fails until the history seed (T018) and the test sets (T017) exist.
"""

from __future__ import annotations

import json
from typing import Any

from db.seeds.generate import GENERATED_DIR
from packages.platform.settings import Settings
from tests.integration.fixtures import open_client, rebuild

RULE_IDS = [f"ELIG-0{n}" for n in range(1, 7)]
OUTCOMES = {"pass", "fail", "missing"}


def _check_trace(where: str, rules: list[dict[str, Any]]) -> None:
    assert [r["rule_id"] for r in rules] == RULE_IDS, f"{where}: not all six rules in order"
    for rule in rules:
        assert rule["outcome"] in OUTCOMES, f"{where}: {rule['rule_id']} outcome"
        assert rule["policy_ref"], f"{where}: {rule['rule_id']} has no policy ref"
        assert isinstance(rule["inputs"], dict), f"{where}: {rule['rule_id']} inputs"
        if rule["outcome"] == "missing":
            assert rule["missing"], f"{where}: {rule['rule_id']} missing without missing items"


async def test_FR_003_05_every_seeded_history_and_test_set_determination_has_six_rule_trace(
    integration_settings: Settings, owner_url: str
) -> None:
    await rebuild(integration_settings)

    async with open_client(owner_url) as client:
        records = [
            doc
            async for doc in client[integration_settings.MONGO_DB]["determinations"].find({})
        ]
    assert records, "the seeded history holds no determinations (FR-003-22)"
    for record in records:
        _check_trace(f"determinations/{record['_id']}", record["rules"])

    history = sorted((GENERATED_DIR / "history").glob("*.json"))
    traced = 0
    for path in history:
        document = json.loads(path.read_text())
        for item in document.get("items", [document]):
            if "rules" in item:
                _check_trace(f"{path.name}", item["rules"])
                traced += 1

    for name in ("known_answers", "redeterminations_due"):
        path = GENERATED_DIR / "testsets" / f"{name}.json"
        assert path.is_file(), f"{path} was not generated (FR-003-13)"
        for item in json.loads(path.read_text())["items"]:
            if "rules" in item:
                _check_trace(f"{name}/{item.get('case_key', '')}", item["rules"])
                traced += 1
