"""Deterministic role tagging from the standardized ICAO section codes (ARCHITECTURE.md §5.2).

Most roles fall straight out of the section number — the corpus's rigid structure is the
asset. Finer within-document tagging (notes, frequency cells) is left for later passes.
"""
from __future__ import annotations

import re

from .models import DataSubtype, Role

# AD 2.x subsection number -> (role, data subtype). Per ICAO Annex 15 AD 2 layout.
_AD2_SUBTYPE = {
    "2.2": DataSubtype.geographic,   # geographical & administrative data
    "2.3": DataSubtype.schedule,     # hours of operation
    "2.12": DataSubtype.geographic,  # runway physical characteristics
    "2.13": DataSubtype.geographic,  # declared distances
    "2.17": DataSubtype.airspace_limits,  # ATS airspace
    "2.18": DataSubtype.frequency,   # ATS communication facilities
    "2.19": DataSubtype.frequency,   # radio navigation & landing aids
    "2.24": None,                    # charts (role overridden to chart below)
}


def tag(section_code: str) -> tuple[Role, DataSubtype | None]:
    """Map a section code like 'ENR 1.6' / 'AD 2.2' / 'GEN 2.4' to a role."""
    sc = section_code.upper().strip()

    # Reference scaffolding: TOCs, amendment records, prefaces, charges.
    if re.search(r"\b(GEN 0|ENR 0|AD 0|GEN 4)\b", sc) or "0.6" in sc:
        return Role.reference, None

    # Charts.
    if re.search(r"\bENR 6\b", sc) or "AD 2.24" in sc:
        return Role.chart, None

    # Aerodrome data.
    m = re.search(r"\bAD 2(\.\d+)?\b", sc)
    if m:
        sub = "2" + (m.group(1) or "")
        if sub == "2.24":
            return Role.chart, None
        return Role.data, _AD2_SUBTYPE.get(sub, DataSubtype.geographic)

    # En-route airspace / warnings.
    if re.search(r"\bENR 2\b", sc):
        return Role.airspace, None
    if re.search(r"\bENR 5\b", sc):
        return Role.warning, None

    # GEN 2.x: mostly tables (units, conversions) — the "7000-as-altitude" distractor.
    if re.search(r"\bGEN 2\b", sc):
        return Role.data, DataSubtype.conversion_table

    # ENR 1.x: general rules & procedures.
    if re.search(r"\bENR 1\b", sc):
        return Role.rule, None

    # Default for legislation articles and anything unmatched.
    return Role.rule, None


_DEFINITION_RE = re.compile(r"«[^»]+»\s*,\s+(si intende|[èe] |un |una |il |lo |l['’])", re.I)


def refine_definition(text: str, role: Role) -> Role:
    """Promote obvious definition passages (e.g. SERA Art. 2, glossary entries)."""
    if role in (Role.rule, Role.reference) and _DEFINITION_RE.search(text):
        return Role.definition
    return role
