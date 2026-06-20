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

## Phase 2 (synthesis + guards) — findings
Built `synth.py` (constrained-decoding JSON via Ollama `format=schema`, `think=False`),
`guards.py` (citation + number integrity), and the `ask` CLI command. Tested live against
**qwen3:4b on the Radeon 860M iGPU** (Vulkan; `OLLAMA_VULKAN=1` + `OLLAMA_IGPU_ENABLE=1`).
- **Constrained JSON works**: bounded, structured output — no 1762-token ramble; claims +
  citations + verbatim_values + gaps.
- **Answers are correct & grounded**: "codice emergenza" → 7700/7600/7500 from ENR 1.6.2.4,
  cited; "volo da diporto" → Legge 106/1985 Art. 1 & 2, cited with normattiva.it deep links.
- **Number-integrity guard works** once scoped: it *passes* real data (squawk codes verified
  present in source) and must *skip* reference numbers (law numbers, years, section ids) —
  scanning all claim-text numbers initially false-flagged "106"/"1985". Fixed by excluding
  years + cited-section reference numbers. Targets DATA, not citations.
- **Latency**: ~1 min/query via the CLI, dominated by per-invocation model loading (fastembed
  e5 + jina reload each process) + cold LLM load — a persistent service would be far faster.
- Minor (left for tuning): model sometimes over-includes tangential chunks, occasionally
  misfiles a citation ref into `verbatim_values` (guard strips it), and emits duplicate gaps.
- iGPU generation ≈ 14 tok/s (memory-bandwidth-bound, ≈ CPU) but keeps the 16 CPU cores free
  for the embedder/reranker — useful non-contention rather than raw speed.

## Production-model swap (BGE-M3 + bge-reranker-v2-m3)
Added a `BACKEND` switch in config (`"torch"` = production via sentence-transformers,
`"fastembed"` = ONNX substitutes). Default is now `torch`. Key implementation note: BGE-M3
takes **no** query/passage prefixes (unlike e5 — the prefixes were dropped for the torch
path). Re-ingested with BGE-M3 (dim 1024, same as e5, so the LanceDB schema is unchanged).
Findings:
- **Ranking improved**: both frequency tables (AD 2.19/2.18) now rank top with positive
  scores; jina had buried them under AD 2.20.
- **Score scale differs**: bge-reranker emits ~0–1 (sigmoid-like) vs jina's negative logits
  → the placeholder abstention thresholds MUST be recalibrated per reranker (§9).
- **Latency**: torch model loading dominates (~58s/query incl. cold loads vs ~10s ONNX) —
  a persistent service is the real fix; per-CLI-invocation reloads are not representative.
- torch install pulled CUDA-bundled wheels despite a CPU hint (unused on this AMD box);
  a leaner build would pin the PyTorch CPU index.

## Abstention threshold calibration (bge-reranker-v2-m3)
`scripts/calibrate_thresholds.py` runs a 14-question labelled set (8 answerable, 6
out-of-corpus) and reports top-reranker-score distributions. Result on the slice corpus:
- Answerable: 0.475–0.999 (median 0.959); out-of-corpus: 0.001–0.420 (median 0.003).
- **Clean gap at ~0.45** → set `TAU_LOW = 0.45` (abstain below), `TAU_HIGH = 0.80` (confident
  above; hedge between). Correctly abstains all 6 unanswerable, keeps all 8 answerable.
- Near-miss: "avvicinamento strumentale per Fiumicino" scored 0.420 (highest unanswerable —
  topically aviation, matched AD 2.22) just below the gap; off-domain scored ~0.001.
- The final tier ANDs reranker score × LLM status × guard flags, so "confident" requires
  strong retrieval AND a committed, fully-verified answer — hedging is the safe default.
- Re-run the script after any reranker or corpus change; the score scale is model-specific.

## Not done (deliberately out of slice scope)
- Docling table reconstruction; full router + gazetteer; empirical threshold calibration
  (placeholders in CLI); conflicting-sources refinement (C); web UI; persistent-service
  latency; leaner CPU-only torch install.

## Run it
```bash
uv run aip-search ingest                                   # build the index (downloads models 1st run)
uv run aip-search query "Qual è la frequenza della torre di Crotone?"   # retrieval only
uv run aip-search ask "Che codice si seleziona sul transponder per un'emergenza?"  # synthesized + cited
```
Requires Ollama running `qwen3:4b` (iGPU via Vulkan: `OLLAMA_VULKAN=1`, `OLLAMA_IGPU_ENABLE=1`).
