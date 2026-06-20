"""Orchestrate parse → role-tag → (size-split) → chunk for the corpus.

`ingest_corpus(full=True)` walks the whole corpus; `full=False` keeps the 3-file slice.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import roles
from .config import AIP_DIR, AIP_MANIFEST, SLICE_FILES, VDS_DIR
from .models import Chunk, Provenance
from .parse import parse_akn, parse_pdf, parse_pdf_generic

MAX_CHARS = 2000  # over-long chunks (legislation pages, big subsections) are split below


def _load_manifest() -> tuple[dict, dict]:
    data = json.loads(AIP_MANIFEST.read_text())
    by_file = {p["output_filename"]: p for p in data.get("pages", [])}
    top = {"airac_cycle": data.get("airac_cycle"), "effective_date": data.get("effective_date")}
    return by_file, top


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", s).strip("-")


def _split_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Split over-long text into ~max_chars windows at whitespace boundaries."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    parts, start = [], 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            sp = text.rfind(" ", start, end)
            if sp > start:
                end = sp
        parts.append(text[start:end].strip())
        start = end
    return [p for p in parts if p]


def _build(raws, *, source_type, id_base, page_id, entity=None, entity_kind=None,
           snapshot_cycle=None, source_url=None, local_path) -> list[Chunk]:
    out: list[Chunk] = []
    for i, rc in enumerate(raws):
        role, subtype = roles.tag(rc.section_code)
        role = roles.refine_definition(rc.text, role)
        for j, piece in enumerate(_split_text(rc.text)):
            prov = Provenance(
                source_type=source_type, section_code=rc.section_code, page_id=page_id,
                entity=entity, entity_kind=entity_kind, entity_agnostic=entity is None,
                airac_effective_date=rc.airac_date, snapshot_cycle=snapshot_cycle,
                page=rc.page, source_url=source_url, local_path=local_path,
            )
            out.append(Chunk(id=f"{id_base}#{i}_{j}", text=piece, role=role,
                             data_subtype=subtype, provenance=prov))
    return out


def _ingest_pdf(path: Path, manifest: dict, top: dict) -> list[Chunk]:
    entry = manifest.get(path.name, {})
    page_id = entry.get("page_id") or path.stem
    m = re.search(r"AD-2-([A-Z]{4})", path.name)
    icao = m.group(1) if m else None
    raws = parse_pdf(str(path), default_section=page_id)
    return _build(raws, source_type="aip", id_base=_slug(path.stem), page_id=page_id,
                  entity=icao, entity_kind="airport" if icao else None,
                  snapshot_cycle=top.get("airac_cycle"), source_url=entry.get("source_url"),
                  local_path=str(path))


def _ingest_legpdf(path: Path) -> list[Chunk]:
    title = path.stem.strip()
    raws = parse_pdf_generic(str(path), section=title[:48])
    return _build(raws, source_type="legpdf", id_base=_slug(title)[:40], page_id=title,
                  local_path=str(path))


def _ingest_akn(path: Path) -> list[Chunk]:
    raws, meta = parse_akn(str(path))
    title = meta.get("title") or path.stem.strip()
    return _build(raws, source_type="akn", id_base=_slug(title)[:40], page_id=title,
                  source_url=meta.get("source_url"), local_path=str(path))


def ingest_corpus(full: bool = True) -> tuple[list[Chunk], dict]:
    manifest, top = _load_manifest()
    report: dict = {"aip_files": 0, "vds_files": 0, "failed": [], "chunks": 0}
    chunks: list[Chunk] = []

    if full:
        aip_files = sorted(AIP_DIR.glob("*.pdf"))
        vds_files = sorted(VDS_DIR.glob("*"))
    else:
        aip_files = [f for f in SLICE_FILES if f.suffix.lower() == ".pdf"]
        vds_files = [f for f in SLICE_FILES if f.suffix.lower() == ".xml"]

    for p in aip_files:
        try:
            chunks += _ingest_pdf(p, manifest, top)
            report["aip_files"] += 1
        except Exception as e:  # noqa: BLE001 — robustness: log and continue
            report["failed"].append((p.name, repr(e)[:120]))

    # AKN XML wins over its PDF twin (skip the duplicate PDF).
    xml_stems = {f.stem.strip() for f in vds_files if f.suffix.lower() == ".xml"}
    for p in vds_files:
        try:
            if p.suffix.lower() == ".xml":
                chunks += _ingest_akn(p)
                report["vds_files"] += 1
            elif p.suffix.lower() == ".pdf" and p.stem.strip() not in xml_stems:
                chunks += _ingest_legpdf(p)
                report["vds_files"] += 1
        except Exception as e:  # noqa: BLE001
            report["failed"].append((p.name, repr(e)[:120]))

    report["chunks"] = len(chunks)
    return chunks, report


def ingest_all() -> list[Chunk]:
    """Backward-compatible slice ingest (3 files)."""
    return ingest_corpus(full=False)[0]
