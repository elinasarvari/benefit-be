"""Training-edition manual and citation table (FR-004-09, FR-004-18, ADR-004-01)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from packages.domain.eligibility import RuleId
from packages.rules.citations import CITATIONS, MANUAL_VERSION
from packages.rules.rule_sets import POLICY_REFS

MANUAL = Path(__file__).parents[3] / "data" / "policy" / "training-manual.md"
NOTICE = "training edition, not official policy"
HEADING = re.compile(r"^## (TM-\d+\.\d+) (.+)$", re.MULTILINE)


def _sections() -> dict[str, str]:
    return {section: title for section, title in HEADING.findall(MANUAL.read_text())}


def test_FR_004_09_01_manual_has_version_date_and_training_notice() -> None:
    text = MANUAL.read_text()

    assert f"Version date: {MANUAL_VERSION.isoformat()}" in text
    assert MANUAL_VERSION == date(2026, 1, 1)
    assert NOTICE in text.splitlines()[0].lower() or NOTICE in text[:400].lower()


def test_FR_004_09_02_manual_has_section_for_each_of_six_rules() -> None:
    sections = _sections()
    text = MANUAL.read_text()
    parts = re.split(r"^## ", text, flags=re.MULTILINE)[1:]

    assert set(POLICY_REFS.values()) <= set(sections)
    assert len(parts) == len(sections)
    assert all(NOTICE in part.lower() for part in parts)


def test_FR_004_18_01_citation_table_has_entry_for_each_of_six_rules() -> None:
    assert set(CITATIONS) == set(RuleId)
    for rule_id, citation in CITATIONS.items():
        assert citation.rule_id is rule_id
        assert citation.section == POLICY_REFS[rule_id]
        assert 1 <= len(citation.section) <= 20
        assert 1 <= len(citation.title) <= 200


def test_FR_004_18_02_citation_matches_manual_heading_and_version_date() -> None:
    sections = _sections()

    for citation in CITATIONS.values():
        assert sections[citation.section] == citation.title
        assert citation.manual_version == MANUAL_VERSION
