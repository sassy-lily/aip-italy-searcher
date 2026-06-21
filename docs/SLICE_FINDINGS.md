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
- **Citations use INTEGER labels** ([1..N], mapped back to chunk-ids), not raw string ids.
  Long ids were fragile: qwen3:8b copied the literal `id=` prefix → the guard rejected the
  nonexistent id and dropped the whole answer. Integers are robust across models.
- **Number guard is PRESENCE-only — a known limit.** It catches fabricated/derived numbers
  but NOT a wrong-but-grounded one: for "VFR non in contatto con l'ATC" the 4B answered with a
  code present in the cited chunk but wrong for the *condition* (it should be the 7000
  conspicuity code). Both 4B and 8B erred (model size isn't the lever; **kept 4B**). The real
  fix is retrieval/chunking that isolates the condition-specific value — same family as the
  Docling-tables gap.

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
`scripts/calibrate_thresholds.py` runs a labelled question set and reports top-reranker-score
distributions. Two calibrations:

**Slice (3 files):** answerable 0.475–0.999, out-of-corpus 0.001–0.420 — a **clean gap** at
~0.45.

**Full corpus (re-calibrated; negatives = foreign airports + off-domain):**
- Answerable: 0.543–0.999 (median 0.962). Off-domain / clearly-absent: 0.002–0.152.
- **OVERLAP**: "approach procedures at JFK" scored **0.838** — a false positive matching an
  Italian airport's AD 2.22 procedures on topical similarity (JFK isn't a detected entity, so
  no hard filter fired). No single threshold separates it from real answers.
- **Key finding**: this empirically proves entity binding (not score thresholds) is the real
  safety mechanism. The score gate catches off-domain/junk; foreign-airport-shaped queries
  need the gazetteer router (block on unresolved-but-airport-shaped entity, §8) — a known gap.
- Chosen: `TAU_LOW = 0.35` (abstains 5/6 negatives, false-abstains none of the 10 answerable),
  `TAU_HIGH = 0.90` (the JFK false-positive lands in *hedged*, not confident).
- The final tier ANDs reranker score × LLM status × guard flags — "confident" needs strong
  retrieval AND a committed, fully-verified answer; hedging is the safe default.
- Re-run the script after any reranker or corpus change; the score scale is model-specific.

**Full corpus, router-aware (≥10/kind; current):** with the gazetteer router live, the 3
foreign-airport negatives now ABSTAIN at routing (no longer score-gated) — the JFK overlap
above is closed upstream, exactly as the "key finding" predicted. Re-derived on 12 answerable
+ 12 off-domain (search-routed): off-domain ≤0.20 (mostly ≤0.018), answerable ≥0.54. The
existing **TAU_LOW=0.35 / TAU_HIGH=0.90 are validated** (unchanged). One answerable ("piste di
Pisa") scored 0.163 against a GEN section instead of AD 2.12 — a semantic-vs-tabular retrieval
miss that TAU_LOW correctly abstains (better than a confident wrong table; motivates Docling
tables).

## Full-corpus ingestion
`aip-search ingest --full` walks the whole corpus. Result:
- **7,568 chunks from 907 AIP + 8 VDS files, 0 parse failures**, ~1h49m (CPU embedding with
  BGE-M3 dominates; one-time per AIRAC cycle).
- AKN/PDF dedupe confirmed: 8 VDS (not 10) — the 2 legislation PDFs with Akoma Ntoso twins
  were skipped in favor of the XML.
- Role distribution: data=3863, rule=2083, airspace=706, warning=611, chart=206,
  reference=78, definition=21 — corpus-shaped, no anomalies; deterministic tagging
  generalized from 3 to 900 files cleanly.

Retrieval validated at scale:
- **Entity hard-filter isolates correctly**: "frequenza TWR di LIRF" → only Roma Fiumicino
  chunks (AD 2.18 #1 at 0.97), out of ~100 airports. Crotone query → LIBC + the
  entity-agnostic escape hatch as designed.
- The original hard question ("codice transponder VFR non in contatto con l'ATC") now
  retrieves the exact section **ENR 1.6.2.10 (Frequency Monitoring Codes)** at top — scale
  improved quality.
- `Retriever` loads 7,568 chunks + builds BM25 in ~0.5s.

## Gazetteer router (router.py, ARCHITECTURE.md §8)
LLM-free router; gazetteer auto-derived from AD filenames (97 ICAOs, 160 name tokens).
`route(query)` → one of search / clarify / abstain, decided before any model loads:
- **Resolve**: ICAO ("LIRF") or unambiguous name → hard entity filter. Set-intersection
  narrows multi-token names ("Roma Fiumicino" → LIRF) while bare "Milano" stays ambiguous.
- **Clarify**: ambiguous city → candidate list ("Milano" → LIMB/LIMC/LIML).
- **Abstain**: foreign/unknown airport ("JFK", "Heathrow") or ICAO not in corpus — **closes
  the false-positive gap the recalibration exposed**, at the router, before retrieval.
- **Open**: airspace queries (TMA/CTR/FIR → entity-agnostic ENR, +airspace role) and general
  questions. Airport-vs-airspace fork prevents wrongly disambiguating "TMA di Milano".
- Clarify/abstain return in ~1s (no embedder/reranker/LLM load).

## Not done (deliberately out of slice scope)
- Router extensions: airspace *entity* resolution (currently keyword-routed, not bound to a
  specific TMA/CTR), richer informal-alias coverage, foreign ICAO detection (only LI** parsed).
- Docling table reconstruction; conflicting-sources refinement (C);
  persistent-service latency (Streamlit keeps models resident per session via
  `@st.cache_resource`, so only the first query is slow — a standalone service is deferred);
  leaner CPU-only torch install.

## Run it
```bash
uv run aip-search ingest --full                            # build the index (downloads models 1st run)
uv run aip-search query "Qual è la frequenza della torre di Crotone?"   # retrieval only
uv run aip-search ask "Che codice si seleziona sul transponder per un'emergenza?"  # synthesized + cited
uv run streamlit run src/aip_search/streamlit_app.py       # web UI
```
Requires Ollama running `qwen3:4b` (iGPU via Vulkan: `OLLAMA_VULKAN=1`, `OLLAMA_IGPU_ENABLE=1`).
The CLI and the Streamlit UI share one pipeline (`answer.py`); the UI handles clarify via
candidate buttons and keeps models resident per session (`@st.cache_resource`).
