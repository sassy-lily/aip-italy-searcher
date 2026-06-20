"""Parsing: PDF via PyMuPDF (per-page column auto-detection) and Akoma Ntoso via lxml.

Slice scope: PyMuPDF stands in for the locked Docling parser. It is enough to validate the
column-extraction unknown and produce real text; Docling (richer tables) comes later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF
from lxml import etree

_AIRAC_RE = re.compile(r"AIRAC effective date\s+([0-9]{1,2}\s+[A-Z]{3}\s+[0-9]{4})")
# Subsection heading, optionally prefixed by a 4-letter ICAO code (e.g. "LIBC AD 2.2").
_HEADING_RE = re.compile(r"^(?:[A-Z]{4}\s+)?((?:GEN|ENR|AD)\s+\d+(?:\.\d+)*)\b")

# Running headers/footers to drop (the AIRAC date is captured separately, page-wide).
_BOILERPLATE = [
    re.compile(r"^AIP - Italia$"),
    re.compile(r"^AIRAC effective date"),
    re.compile(r"ENAV - Roma"),
    re.compile(r"^\(A\d+[/-]\d+\)$"),
    # Page running-header/footer: "ENR 1.6 - 2", "AD 2 LIBC 1 - 2", "AD 2 LIBC 2 ­ 1".
    re.compile(r"^(?:[A-Z]{4}\s+)?(?:GEN|ENR|AD)[\s\d.A-Z]*[-–­]\s*\d+$"),
]


def _is_boilerplate(line: str) -> bool:
    return any(p.search(line) for p in _BOILERPLATE)


@dataclass
class RawChunk:
    section_code: str
    text: str
    page: int
    airac_date: str | None = None
    extra: dict = field(default_factory=dict)


def _page_lines(page: "fitz.Page") -> list[str]:
    """Return Italian text lines for a page, auto-detecting two-column prose layout.

    Two-column prose (ENR/GEN): keep only the left (Italian) column.
    Single-column / stacked-bilingual tables (AD): keep everything (values are
    language-neutral; the Italian label is present alongside the English one).
    """
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    mid = page.rect.width / 2
    right = [b for b in blocks if b[0] >= mid and len(b[4].strip()) > 20]
    two_column = len(right) >= 2

    kept = [b for b in blocks if b[0] < mid] if two_column else blocks
    kept.sort(key=lambda b: (round(b[1] / 3), b[0]))  # reading order: top→bottom, left→right
    lines: list[str] = []
    for b in kept:
        for ln in b[4].splitlines():
            ln = ln.strip()
            if ln and not _is_boilerplate(ln):
                lines.append(ln)
    return lines


def parse_pdf(path: str, default_section: str) -> list[RawChunk]:
    """Split a PDF into subsection-level chunks, keeping Italian text."""
    doc = fitz.open(path)
    chunks: list[RawChunk] = []
    cur: RawChunk | None = None

    for pno in range(doc.page_count):
        page = doc[pno]
        m = _AIRAC_RE.search(page.get_text())
        airac = m.group(1) if m else None

        for ln in _page_lines(page):
            h = _HEADING_RE.match(ln)
            if h:
                code = h.group(1)
                # Collapse the duplicated IT/EN heading lines: same code → continuation.
                if cur is not None and cur.section_code == code:
                    cur.text += "\n" + ln
                    continue
                cur = RawChunk(section_code=code, text=ln, page=pno + 1, airac_date=airac)
                chunks.append(cur)
            elif cur is not None:
                cur.text += "\n" + ln
                if cur.airac_date is None and airac:
                    cur.airac_date = airac
            else:
                # Preamble before the first heading → attach to the default section.
                cur = RawChunk(section_code=default_section, text=ln, page=pno + 1, airac_date=airac)
                chunks.append(cur)

    for c in chunks:
        c.text = re.sub(r"[ \t]+", " ", c.text).strip()
    return [c for c in chunks if len(c.text) > 25]


def parse_pdf_generic(path: str, section: str) -> list[RawChunk]:
    """Parse a non-AIP PDF (legislation) into one chunk per page, keeping Italian text.

    Legislation PDFs lack the AIP GEN/ENR/AD heading structure, so we don't try to split
    on it — page-level chunks, size-split downstream.
    """
    doc = fitz.open(path)
    out: list[RawChunk] = []
    for pno in range(doc.page_count):
        text = re.sub(r"[ \t]+", " ", "\n".join(_page_lines(doc[pno]))).strip()
        if len(text) > 25:
            out.append(RawChunk(section_code=f"{section} p.{pno + 1}", text=text, page=pno + 1))
    return out


# --- Akoma Ntoso ---------------------------------------------------------------

_AKN_NS = {"akn": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}


def _text_of(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def parse_akn(path: str) -> tuple[list[RawChunk], dict]:
    """Parse an Akoma Ntoso act into per-article chunks + document-level metadata."""
    tree = etree.parse(path)
    root = tree.getroot()

    def first(xpath: str) -> str | None:
        r = root.xpath(xpath, namespaces=_AKN_NS)
        return r[0] if r else None

    urn = first('.//akn:FRBRalias[@name="urn:nir"]/@value')
    frbr_uri = first(".//akn:FRBRWork/akn:FRBRuri/@value")
    title = first('.//*[local-name()="span"][@property="eli:title"]/@content')
    meta = {
        "urn": urn,
        "frbr_uri": frbr_uri,
        "title": (title or "").strip(),
        "source_url": (
            f"https://www.normattiva.it/uri-res/N2Ls?{urn}" if urn else None
        ),
    }

    chunks: list[RawChunk] = []
    articles = root.xpath(".//akn:article", namespaces=_AKN_NS)
    for art in articles:
        eid = art.get("eId") or art.get("GUID") or ""
        num_el = art.xpath("./akn:num", namespaces=_AKN_NS)
        num = _text_of(num_el[0]) if num_el else eid
        text = _text_of(art)
        if len(text) < 20:
            continue
        chunks.append(RawChunk(section_code=num, text=text, page=1, extra={"eid": eid}))
    return chunks, meta
