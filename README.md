# AIP-Search

A fully-local, **Italian-only** findability aid over the Italian AIP (ENAV) and the
legislation governing **VDS** (*volo da diporto o sportivo*). Ask a question in Italian; get a
short synthesized answer with **citations and deep links back to the source**, so you can
verify every statement against the authoritative document.

> ⚠️ **Strumento di consultazione, non per uso operativo.** This is a search/lookup aid, **not
> a flight-planning tool** and not an authoritative source. Every answer is citation-first —
> always verify against the official AIP / AIRAC edition. Runs entirely on-device; the only
> network use is the optional "open the official source" links (ENAV / EUR-Lex / normattiva.it).

## What it does

- **Finds, doesn't replace.** Saves you from sifting ~900 PDFs to locate a fact.
- **Citation-first.** Each claim maps to a retrieved chunk, rendered with a deep link.
- **Abstains rather than hallucinates.** Three tiers — confident / hedged-pointer / hard-abstain.
- **Never mixes airports.** Entity is a hard filter; a question about one aerodrome never
  returns another's data. Ambiguous places ("Milano") trigger a clarify prompt; foreign
  airports ("JFK") are refused.
- **Never invents numbers.** Frequencies, codes and coordinates are copied verbatim from a
  cited chunk and verified by an integrity guard.
- **Italian only**, on **CPU** (the LLM optionally uses an AMD iGPU via Vulkan).

## How it works

```
Question (IT)
 → ROUTER (gazetteer, LLM-free): resolve entity / clarify / abstain / open + target roles
 → RETRIEVAL: hybrid (BGE-M3 dense ∪ BM25) → RRF → metadata gate (hard entity filter,
              role boost) → cross-encoder rerank (bge-reranker-v2-m3)
 → ABSTENTION: 3 tiers by reranker score (calibrated)
 → SYNTHESIS: thin LLM (Ollama qwen3:4b), constrained-JSON output (claims+citations+gaps)
 → GUARDS: citation-integrity + number-integrity (deterministic)
 → cited answer + AIRAC currency + deep links  (CLI or Streamlit)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design (with rationale) and
[`docs/SLICE_FINDINGS.md`](docs/SLICE_FINDINGS.md) for what the implementation actually found.

## Tech stack

| Layer | Choice |
|---|---|
| Env / deps | Python 3.12 · [`uv`](https://docs.astral.sh/uv/) |
| Parse | PyMuPDF (PDF, column-aware) · lxml (Akoma Ntoso XML) |
| Embed (dense) | **BGE-M3** (sentence-transformers) — `BACKEND="fastembed"` swaps to multilingual-e5 (ONNX) |
| Sparse | BM25 (`rank-bm25`) |
| Store | LanceDB |
| Rerank | **bge-reranker-v2-m3** cross-encoder (fastembed: jina-reranker-v2) |
| LLM | **qwen3:4b** via Ollama (constrained decoding) |
| UI | Streamlit · CLI (Typer) |

## Requirements

- Linux, Python ≥ 3.12, `uv`. Tested on 16 cores / 32 GB RAM, CPU-only.
- [Ollama](https://ollama.com) with a model pulled: `ollama pull qwen3:4b`.
  - Optional AMD iGPU acceleration (Vulkan): set `OLLAMA_VULKAN=1` and `OLLAMA_IGPU_ENABLE=1`
    on the Ollama service.
- The corpus (read-only, **not** in this repo): paths are configured in
  [`src/aip_search/config.py`](src/aip_search/config.py) — the ENAV AIP snapshot directory
  (PDFs + `manifest.json`) and the VDS legislation directory.

## Setup & usage

```bash
uv sync                                   # create the venv and install deps

# Build the index (first run downloads the embedder/reranker; full corpus ≈ a couple of hours on CPU)
uv run aip-search ingest --full           # omit --full for the small dev slice

# Query
uv run aip-search query "Qual è la frequenza della torre di Crotone?"   # retrieval only
uv run aip-search ask   "Che codice transponder si usa in VFR non in contatto con l'ATC?"  # synthesized + cited

# Web UI (first query loads models, then stays resident for the session)
uv run streamlit run src/aip_search/streamlit_app.py
```

The CLI and the UI share one pipeline ([`answer.py`](src/aip_search/answer.py)).

## Configuration

- **Backend**: `BACKEND` in `config.py` — `"torch"` (production: BGE-M3 + bge-reranker-v2-m3)
  or `"fastembed"` (torch-free ONNX substitutes).
- **Corpus paths**: `AIP_DIR`, `VDS_DIR` in `config.py`.
- **Abstention thresholds**: calibrated empirically — re-derive with
  `uv run python scripts/calibrate_thresholds.py` after any reranker or corpus change (the
  reranker score scale is model-specific).

## Project layout

```
src/aip_search/
  config.py      paths, model names, backend switch
  models.py      Chunk / Provenance / Role (data model)
  roles.py       deterministic role tagging from section codes
  parse.py       PyMuPDF (column-aware) + Akoma Ntoso (lxml)
  ingest.py      walk corpus → parse → tag → chunk → build report
  index.py       embed + LanceDB
  retrieve.py    hybrid + RRF + metadata gate + rerank
  router.py      gazetteer + route (resolve / clarify / abstain / open)
  synth.py       constrained-JSON synthesis via Ollama
  guards.py      citation- + number-integrity
  answer.py      end-to-end pipeline shared by CLI & UI
  cli.py         Typer CLI (ingest / query / ask)
  streamlit_app.py   web UI
scripts/calibrate_thresholds.py   abstention-threshold calibration
docs/            ARCHITECTURE.md · SLICE_FINDINGS.md
```

## Status

Design complete; the vertical slice is implemented and scaled to the full corpus (~7,500
indexed chunks from 907 AIP PDFs + VDS legislation), on the production model stack. Known gaps
and roadmap are tracked in `docs/SLICE_FINDINGS.md` (Docling table parsing, conflicting-source
handling, packaging).

## License

See [`LICENSE`](LICENSE).
