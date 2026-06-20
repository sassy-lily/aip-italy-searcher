"""Paths, models, and runtime config."""
from __future__ import annotations

import os
from pathlib import Path

# Local & offline by design: once the embedder/reranker are cached, never contact the
# Hugging Face Hub. This removes the per-run "unauthenticated requests to the HF Hub" warning
# and the latency of its update check. Set before any HF library is imported (config is
# imported before the lazy sentence-transformers/fastembed imports). For the FIRST-time model
# download, override with `HF_HUB_OFFLINE=0`.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

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
