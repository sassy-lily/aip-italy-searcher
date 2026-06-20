"""Orchestrate parse → role-tag → chunk for the slice's file set."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import roles
from .config import AIP_DIR, AIP_MANIFEST, SLICE_FILES
from .models import Chunk, Provenance
from .parse import parse_akn, parse_pdf


def _load_manifest() -> tuple[dict, dict]:
    """Return (by_filename, top_level) from the AIP manifest."""
    data = json.loads(AIP_MANIFEST.read_text())
    by_file = {p["output_filename"]: p for p in data.get("pages", [])}
    top = {"airac_cycle": data.get("airac_cycle"), "effective_date": data.get("effective_date")}
    return by_file, top


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", s).strip("-")


def _ingest_pdf(path: Path, manifest: dict, top: dict) -> list[Chunk]:
    entry = manifest.get(path.name, {})
    page_id = entry.get("page_id") or path.stem
    source_url = entry.get("source_url")
    icao = None
    m = re.search(r"AD-2-([A-Z]{4})", path.name)
    if m:
        icao = m.group(1)

    raws = parse_pdf(str(path), default_section=page_id)
    out: list[Chunk] = []
    for i, rc in enumerate(raws):
        role, subtype = roles.tag(rc.section_code)
        role = roles.refine_definition(rc.text, role)
        prov = Provenance(
            source_type="aip",
            section_code=rc.section_code,
            page_id=page_id,
            entity=icao,
            entity_kind="airport" if icao else None,
            entity_agnostic=icao is None,
            airac_effective_date=rc.airac_date,
            snapshot_cycle=top.get("airac_cycle"),
            page=rc.page,
            source_url=source_url,
            local_path=str(path),
        )
        out.append(
            Chunk(
                id=f"{_slug(page_id)}#{i}",
                text=rc.text,
                role=role,
                data_subtype=subtype,
                provenance=prov,
            )
        )
    return out


def _ingest_akn(path: Path) -> list[Chunk]:
    raws, meta = parse_akn(str(path))
    out: list[Chunk] = []
    for i, rc in enumerate(raws):
        role, subtype = roles.tag(rc.section_code)
        role = roles.refine_definition(rc.text, role)
        prov = Provenance(
            source_type="akn",
            section_code=rc.section_code,
            page_id=meta.get("title") or "Legge 106/1985",
            entity=None,
            entity_agnostic=True,
            source_url=meta.get("source_url"),
            local_path=str(path),
        )
        out.append(
            Chunk(
                id=f"L106-1985#{i}",
                text=rc.text,
                role=role,
                data_subtype=subtype,
                provenance=prov,
            )
        )
    return out


def ingest_all() -> list[Chunk]:
    manifest, top = _load_manifest()
    chunks: list[Chunk] = []
    for path in SLICE_FILES:
        if path.suffix.lower() == ".pdf":
            chunks += _ingest_pdf(path, manifest, top)
        elif path.suffix.lower() == ".xml":
            chunks += _ingest_akn(path)
    return chunks
