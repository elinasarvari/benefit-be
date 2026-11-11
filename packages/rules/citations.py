"""Citation table: each eligibility rule's manual section (FR-004-18).

Contract stub (MVP contracts PR): empty until 004a adds the training-edition manual's sections.
Explanations take their citations only from this table, and say "no citation available" for a
rule that has no entry (004 US4 scenario 3).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from packages.domain.eligibility import RuleId
from packages.domain.explanation import Citation

CITATIONS: Mapping[RuleId, Citation] = MappingProxyType({})
