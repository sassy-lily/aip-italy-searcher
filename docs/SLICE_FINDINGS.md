# Vertical Slice — Findings

> Phase 1 (ingest → index → retrieve, no LLM) built and run on real files, 2026-06-20.
> Branch: `feat/vertical-slice`.

## What was built
A torch-free, CPU-only Phase-1 pipeline: parse 3 real files → role-tag → subsection chunk
→ embed (multilingual-e5-large via fastembed/ONNX) → LanceDB → hybrid (dense ∪ BM25, RRF)
→ metadata gate (entity hard-filter, role soft-boost) → cross-encoder rerank
(jina-reranker-v2-base-multilingual) → CLI with three-tier abstention + ENAV deep links.

Files: `0443_AD-2-LIBC---CROTONE-1.pdf` (table-heavy AD page), `0036_ENR-1.6.pdf` (SSR
procedures), `Legge 106/1985.xml` (Akoma Ntoso). → 55 chunks.

## Stack substitutions (slice vs locked design)
| Locked (ARCHITECTURE.md) | Slice substitute | Reason |
|---|---|---|
| Docling (PDF) | PyMuPDF | torch-free; enough to test column extraction |
| BGE-M3 (dense) | intfloat/multilingual-e5-large | fastembed ships e5, not BGE-M3 (needs torch) |
| bge-reranker-v2-m3 | jinaai/jina-reranker-v2-base-multilingual | same; both available in fastembed/ONNX |
| Ollama LLM | — (Phase 2) | needs system install |

Interfaces are identical → swapping to the torch-based locked models later is localized.

## The three validate-in-slice unknowns — RESOLVED
1. **Column-extraction fidelity ✓.** Per-page auto-detection works: ENR prose is a true
   two-column layout (x-split keeps Italian cleanly); AD pages are stacked-bilingual tables
   (kept whole; values are language-neutral). Subsections split correctly (ENR 1.6.1 …
   1.6.5.1; AD 2.1 … 2.25).
2. **Role-tagging accuracy ✓.** Deterministic section-code mapping produced correct roles:
   `data/frequency` (AD 2.18/2.19), `data/geographic` (AD 2.2), `data/schedule` (AD 2.3),
   `chart` (AD 2.24), `rule` (ENR 1.6, law articles).
3. **End-to-end CPU latency — MEASURED.** Retrieval + rerank ≈ **10 s/query**, dominated by
   the cross-encoder reranker over ~15 candidates. Embedding the query (e5-large) is the
   other cost. Implication: the reranker is the latency lever (fewer candidates / lighter
   model). With LLM synthesis added, expect ~20–30 s total — tolerable for a lookup tool.

## Other findings
- **The Milan "Frequency Monitoring Codes" answer is in extractable text** (ENR 1.6.2.10 +
  1.6.3.1 Milano ACC codes), **not chart-locked** as earlier assumed. Always check text first.
- **Per-page AIRAC dates vary within one aerodrome** (AD 2.2 = 26 DEC 2024, AD 2.18 = 30 OCT
  2025, AD 2.22 = 11 JUN 2026) — the citation-currency design point, confirmed real.
- **Entity hard-filter works**: "frequenza torre di Crotone" → resolved LIBC → only Crotone
  data returned (never another airport).
- **Boilerplate filtering needed**: page running-headers (`AD 2 LIBC 1 - 2`) matched the
  heading regex and spawned junk chunks; filtered out (76 → 55 chunks).
- **Reranker imperfections**: jina occasionally mis-orders (AD 2.20 above the frequency
  table 2.18). Expected; the locked bge-reranker-v2-m3 may differ.
- fastembed e5-large now uses mean pooling (a behaviour-change warning) — correct for e5.

## Not done (deliberately out of slice scope)
- Docling table reconstruction; full router + gazetteer; LLM synthesis + integrity guards
  (Phase 2, needs Ollama); empirical threshold calibration (placeholders in CLI);
  conflicting-sources refinement (C); the production BGE-M3 / bge-reranker-v2-m3 models.

## Run it
```bash
uv run aip-search ingest                 # build the index (downloads models on first run)
uv run aip-search query "Qual è la frequenza della torre di Crotone?"
```
