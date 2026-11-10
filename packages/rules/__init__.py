"""Eligibility rules engine (contracts/rules-interface.md). Pure: no I/O, clock or RNG."""

from __future__ import annotations

from packages.rules.engine import ENGINE_VERSION, evaluate
from packages.rules.income import monthly_income_from_stub
from packages.rules.rule_sets import rule_set_for

__all__ = ["ENGINE_VERSION", "evaluate", "monthly_income_from_stub", "rule_set_for"]
