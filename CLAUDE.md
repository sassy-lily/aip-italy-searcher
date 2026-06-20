# AIP-Search — project guide

A fully-local, **Italian-only** findability aid over the Italian AIP (ENAV) + VDS
legislation. Given an Italian question, it returns a short synthesized answer with
**citations and deep links back to source**. It is a **reference/lookup tool, not a
flight planner**.

> **Design source of truth:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Read it
> before making design decisions — most are already settled there *with rationale*. Don't
> re-litigate locked decisions; if you think one is wrong, surface the conflict against the
> recorded rationale rather than silently diverging.

## Status
Design complete; **no application code written yet**. The intended first step is the
**vertical slice** described in `ARCHITECTURE.md` §15 (a few real files → index → one
answer end-to-end). Do not scaffold or build until explicitly asked.

## Core constraints (non-negotiable)
- **Local & offline.** Everything runs on-device. The only network use is optional
  "open the official source" links (ENAV / EUR-Lex / normattiva.it).
- **CPU-only**, target laptop = 16 cores / 32 GB RAM. No GPU. Inference is
  memory-bandwidth bound — favour small models, tight context, few reranked chunks.
- **Italian only** for questions, answers, and the indexed text (keep the Italian column,
  discard English).
- **Safety framing.** Aeronautical data; always show the disclaimer ("strumento di
  consultazione, non per uso operativo") and the AIRAC currency.

## Critical invariants (these are correctness/safety rules — never violate)
1. **Never mix entities.** A question about one airport/airspace must never return another's
   data. Entity is a **hard filter** (resolved entity OR entity-agnostic), never a soft boost.
2. **Numbers are copied verbatim, never derived.** Frequencies, codes, coordinates,
   altitudes must come straight from a cited chunk. The number-integrity guard enforces this.
3. **Abstain rather than hallucinate.** Three-tier output (confident / hedged-pointer /
   hard-abstain). If unsure, hedge and point to the source; don't invent.
4. **Every claim is cited.** Citations are the safety mechanism, not decoration. Strip any
   citation that doesn't map to a retrieved chunk.
5. **Never mix AIRAC cycles.** Tag every chunk with its cycle; answer from the current one;
   surface the per-page effective date.
6. **No OCR / no VLM.** Charts: use easily-extractable vector text, otherwise *punt* (link
   the user to the chart). Don't add an OCR/vision pipeline.

## Tech stack (locked — see ARCHITECTURE.md for why)
- **Language:** Python. Use **`uv`** for env/deps.
- **Parse:** Docling (PDF, column-aware) · cobalt + lxml (Akoma Ntoso XML).
- **Embed:** BGE-M3 (dense) · BM25 (sparse, exact tokens). **Store:** LanceDB (single store;
  its FTS serves the sparse leg — validate exact-token tokenization).
- **Rerank:** bge-reranker-v2-m3 (cross-encoder).
- **LLM:** served via **Ollama**; default **Qwen3 4B**, A/B **Minerva 7B** (Sapienza GGUF).
  Generation is thin: **constrained decoding** (Ollama `format` / llama.cpp GBNF) to a
  fixed JSON schema, then deterministic integrity guards + rendering.
- **UI:** leaning Streamlit (not final). Framework LlamaIndex vs hand-rolled = build-time call.

## Corpus (read-only inputs; do not modify)
- AIP: `~/Documents/aip-downloader/downloads/2026-06-11_A06-26/` — 907 PDFs + `manifest.json`
  (per-page section, page_id, source_url, content_hash; top-level airac_cycle, effective_date,
  delta). The manifest is the provenance + incremental-update backbone — read from it, don't
  re-derive what it already provides.
- VDS legislation: `~/Downloads/vds/` — 2 Akoma Ntoso XML + 8 PDFs.

## Development conventions
- Inherit the global working agreement (`~/.claude/CLAUDE.md`): Conventional Commits, commit
  attribution trailer, small logical commits, **never push without
  explicit confirmation**, no secrets/artifacts committed. Fedora + `dnf` + systemd userland.
- **Validation:** the user is a domain expert who validates manually. Maintain the **ingest
  build report** (per-file parsed/chart-punt/failed + role distribution) as the primary
  validation surface — ingest-time tagging errors are baked in until re-ingest. Seed a small
  (~20–30) Italian question set for retrieval/abstention regression across AIRAC updates.
- **Reranker/abstention thresholds are calibrated empirically** after models are fixed — do
  not hardcode magic numbers; derive `τ_low`/`τ_high` from the validation set.
- Build/test/run commands: **TBD** — add them here once the project is scaffolded.

## Common pitfalls (project-specific)
- Don't treat the corpus as generic PDFs — exploit the standardized section codes, filenames,
  manifest, and Akoma Ntoso eIds (they give citations/roles/versioning for free).
- Don't let `data/conversion-table` chunks (GEN 2.x) pollute operational answers — they are
  the "7000-as-altitude" distractor; suppress them.
- Don't strip query conditions ("non in contatto", "di notte", "VFR") in the router — the
  reranker/LLM need them.
- Don't assume cross-corpus sources agree (AIP vs SERA vs national law can conflict — open
  refinement, see ARCHITECTURE.md §14.1).
