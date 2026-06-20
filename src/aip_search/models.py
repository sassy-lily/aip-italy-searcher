"""Core data model: provenance × role (the two orthogonal axes from ARCHITECTURE.md §5)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Role(str, Enum):
    rule = "rule"
    procedure = "procedure"
    definition = "definition"
    data = "data"
    airspace = "airspace"
    warning = "warning"
    chart = "chart"
    reference = "reference"


class DataSubtype(str, Enum):
    frequency = "frequency"
    geographic = "geographic"
    schedule = "schedule"
    airspace_limits = "airspace-limits"
    conversion_table = "conversion-table"


class Provenance(BaseModel):
    source_type: str  # "aip" | "akn" | "legpdf"
    section_code: str | None = None  # "ENR 1.6", "AD 2 LIBC", "Legge 106/1985 art. 5"
    page_id: str | None = None
    entity: str | None = None  # ICAO code or airspace id
    entity_kind: str | None = None  # "airport" | "airspace"
    entity_agnostic: bool = True
    airac_effective_date: str | None = None  # per-page, e.g. "26 DEC 2024"
    snapshot_cycle: str | None = None
    page: int | None = None
    source_url: str | None = None  # official online source (deep link)
    local_path: str = ""


class Chunk(BaseModel):
    id: str
    text: str
    role: Role
    data_subtype: DataSubtype | None = None
    lang: str = "it"
    provenance: Provenance

    def flat_meta(self) -> dict:
        """Flatten to scalar columns for LanceDB storage/filtering."""
        p = self.provenance
        return {
            "id": self.id,
            "text": self.text,
            "role": self.role.value,
            "data_subtype": self.data_subtype.value if self.data_subtype else "",
            "lang": self.lang,
            "source_type": p.source_type,
            "section_code": p.section_code or "",
            "page_id": p.page_id or "",
            "entity": p.entity or "",
            "entity_kind": p.entity_kind or "",
            "entity_agnostic": p.entity_agnostic,
            "airac_effective_date": p.airac_effective_date or "",
            "snapshot_cycle": p.snapshot_cycle or "",
            "page": p.page if p.page is not None else -1,
            "source_url": p.source_url or "",
            "local_path": p.local_path,
        }
