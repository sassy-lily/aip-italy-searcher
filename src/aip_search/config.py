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

# Embedding/rerank backend:
#   "torch"     → production locked models BGE-M3 + bge-reranker-v2-m3 (sentence-transformers)
#   "fastembed" → ONNX substitutes multilingual-e5-large + jina-reranker-v2 (torch-free)
BACKEND = "torch"

_DENSE = {"torch": "BAAI/bge-m3", "fastembed": "intfloat/multilingual-e5-large"}
_RERANK = {"torch": "BAAI/bge-reranker-v2-m3", "fastembed": "jinaai/jina-reranker-v2-base-multilingual"}
DENSE_MODEL = _DENSE[BACKEND]
RERANK_MODEL = _RERANK[BACKEND]

# Generation LLM, served by Ollama (on the Radeon 860M iGPU via Vulkan).
LLM_MODEL = "qwen3:4b"
