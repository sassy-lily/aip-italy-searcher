"""Paths and the small fixed file set used by the vertical slice."""
from __future__ import annotations

from pathlib import Path

# Read-only corpus inputs.
AIP_DIR = Path("/home/andrea/Documents/aip-downloader/downloads/2026-06-11_A06-26")
VDS_DIR = Path("/home/andrea/Downloads/vds")
AIP_MANIFEST = AIP_DIR / "manifest.json"

# Where the slice writes its index.
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
LANCE_DIR = DATA_DIR / "lancedb"

# The handful of real files the slice ingests (one table-heavy AD page, one ENR
# prose section matching our sample question, one Akoma Ntoso law).
SLICE_FILES = [
    AIP_DIR / "0443_AD-2-LIBC---CROTONE-1.pdf",
    AIP_DIR / "0036_ENR-1.6.pdf",
    VDS_DIR / "Legge ordinaria numero 106 del 25 marzo 1985 .xml",
]

# fastembed (ONNX) models — multilingual, torch-free. Slice substitutes for the
# locked BGE-M3 / bge-reranker-v2-m3 (which need torch); interfaces are identical.
DENSE_MODEL = "intfloat/multilingual-e5-large"
RERANK_MODEL = "jinaai/jina-reranker-v2-base-multilingual"
