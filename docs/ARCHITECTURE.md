# AIP-Search — Architecture & Design

> **Status:** Design complete (brainstorming phase). No code written yet.
> **Date:** 2026-06-20
> **Reference corpus snapshot:** AIRAC cycle `A06-26`, effective 2026-06-11.

This document records the full design agreed during the design session. It is the
authoritative reference for implementation. Decisions are marked **LOCKED**; a few
minor points remain build-time choices, and three assumptions are explicitly flagged as
*validate-in-slice* (resolvable only by running code, not by further design).

---

## 1. Purpose

AIP-Search is a **fully-local, Italian-only findability aid** over a corpus of Italian
aeronautical and aviation-legal documents. Given a natural-language question in Italian,
it returns a **short synthesized answer with citations and deep links back to the source
material**, so the user can verify every statement against the authoritative document.

**What it is:** a domain-specific search/lookup tool that saves the user from manually
sifting through ~900 PDFs to find a fact.

**What it is *not*:** a flight-planning tool, nor an authoritative answer engine. It is a
**reference aid**. Every answer is citation-first; the user is always expected to
cross-check against the linked source. A visible disclaimer states this and that the
authoritative source is the official AIP / AIRAC edition.

**Runtime constraints:** runs entirely locally and offline (except optional links to
official online sources). Target hardware: a laptop with **16 cores / 32 GB RAM, CPU-only
inference** (no GPU).

---

## 2. Corpus

### 2.1 Italian AIP (ENAV)
- Location: `…/aip-downloader/downloads/2026-06-11_A06-26/`
- **907 PDFs** — 471 `AD` (aerodromes), 408 `ENR` (en-route), 28 `GEN` (general) — plus a
  `manifest.json` and an `index.html`.
- PDFs are **digital text, not scans**, laid out **bilingually in parallel columns**
  (Italian left, English right). We keep the Italian column and discard the English.
- Highly standardized per **ICAO Annex 15** (section numbering `GEN/ENR/AD x.y`).
- **Filenames encode structure**, e.g. `0443_AD-2-LIBC---CROTONE-1.pdf` →
  ordering `0443`, section `AD`, aerodrome chapter `2`, ICAO `LIBC`, name `CROTONE`,
  sheet `1`.
- A few pages are **charts/maps** (e.g. `ENR 6.x`, `AD 2.24`). Some are extractable
  vector text; some (`ENR 6.3`, `ENR 6.4`) are **raster images** with no text layer.
- Each page also carries its **own per-page AIRAC effective date** in its header
  (e.g. "AIRAC effective date 26 DEC 2024"), distinct from the snapshot cycle — a page is
  only re-issued when it changes.

#### The manifest (key asset)
`manifest.json` provides, per page: `section`, `page_id` (e.g. "GEN 0.1"), official ENAV
`source_url`, `content_hash` (sha256), `byte_size`, `last_modified`, `status`; and at top
level `airac_cycle`, `effective_date`, `source_landing_url`, and a `delta` block. This
gives us provenance, source links, and incremental-update diffing essentially for free.

### 2.2 VDS legislation
- Location: `…/Downloads/vds/`
- **10 files**, of which **only 2 are Akoma Ntoso XML** (`Legge 106/1985`, `DPR 133/2010`,
  from Normattiva — AKN 3.0 namespace, with FRBR / ELI / `urn:nir` metadata). The other
  **8 are PDFs** (ENAC *Regolamento tecnico* Titoli 1/3/4 — Italian only; EU Reg.
  923/2012 (SERA) & 2016/1185 — bilingual; *Regole dell'aria* ed. 4).
- Theme: **VDS** — *volo da diporto o sportivo* (recreational/sport flight) and the rules
  governing it.

---

## 3. Design principles (the through-lines)

These three principles drove most decisions and should guide implementation:

1. **Hard-filter the verifiable, soft-boost the inferred.** Categorical, checkable facts
   (which airport, which AIRAC version, source type) are enforced as hard filters; inferred
   signals (query intent → role relevance) are soft boosts that a downstream stage can
   recover from. This is the source of the core safety guarantee (*never return one
   airport's data for a question about another*).
2. **The corpus's rigid standardization is the asset.** Section codes, Akoma Ntoso eIds,
   the manifest, and the parallel IT/EN columns hand us citations, role tags, versioning,
   and entity lists almost for free. This is why a hard problem stays laptop-sized.
3. **Thin generation, fat retrieval.** Because the goal is "find it and cite it," not "be
   the authority," the LLM is a thin synthesizer over strong retrieval. This lowers
   hallucination risk and makes CPU-only viable. **Citations are not decoration — they are
   the safety mechanism.**

---

## 4. High-level architecture

### 4.1 Query-time flow
```
Question (Italian)
 → ROUTER (LLM-free): intent → target roles · entity resolution (gazetteer,
                      block-and-ask on ambiguity) · jargon expansion (sparse leg)
                      [Clarify turn if entity required but missing/ambiguous]
 → RETRIEVAL ENGINE: hybrid (BGE-M3 dense ∪ BM25 sparse) → RRF fusion
                      → metadata gate (HARD-filter entity/version/reference;
                        SOFT-boost roles; suppress conversion-tables)
                      → cross-encoder rerank (bge-reranker-v2-m3)
 → ABSTENTION (3 tiers by reranker score): confident / hedged-pointer / hard-abstain
                      (reranker judges evidence-sufficiency; LLM judges answer-presence)
 → SYNTHESIS (thin, constrained-decoding): structured claims + citations + verbatim values
 → INTEGRITY GUARDS: citation-integrity + number-integrity (deterministic post-processing)
 → PRESENTATION: currency+disclaimer banner · answer with inline [n] · source cards
                 (role tag · AIRAC date · snippet · deep link)
```

### 4.2 Build-time (ingestion) flow
```
Manifest + files
 → provenance bootstrap (manifest / filename / AKN FRBR-urn)
 → parse (Docling column-aware; per-page chart detection; cobalt for AKN)
 → role-tag (deterministic from structure)
 → chunk (subsection-level, stable ids)
 → embed (BGE-M3 dense) + BM25 sparse
 → LanceDB single store
 → build report (validation surface)
Incremental update each AIRAC cycle via content-hash diff (only the delta re-embeds).
```

---

## 5. Data model

Two **orthogonal axes**: *provenance* (where a chunk is from) and *role* (what it
functionally is). A chunk is one point in (provenance × role) space.

### 5.1 Provenance (mostly free from manifest/filenames)
`source_type` · `section_code` (e.g. "ENR 1.6") · `entity` (ICAO code / airspace id) ·
`entity_specific_vs_agnostic` flag · `airac_effective_date` (per-page) · `snapshot_cycle` ·
`page` · `source_url`.

### 5.2 Role taxonomy (LOCKED)
Primary role + optional secondary tags; `note` is a **bound attribute** (never retrieved
detached from its parent), not a standalone role.

| Role | What it is | Detection (mostly deterministic) |
|---|---|---|
| `rule` | Normative provision (obligation/prohibition) | ENR 1.x, SERA annex, law articles + modal verbs |
| `procedure` | Sequenced operational steps | numbered steps + procedural sections |
| `definition` | Terminology | `«term», …` pattern; SERA Art. 2; GEN 2.1/2.2 |
| `data` | Tabular facts (sub-typed below) | table layout + labeled columns + section code |
| `airspace` | Lateral/vertical limits + class of a volume | ENR 2.x, AD 2.17 |
| `warning` | Prohibited/restricted/danger areas, obstacles | ENR 5.x + area codes `LI-P/R/D nn` |
| `chart` | Graphical content | low text-density + image objects (per-page) |
| `reference` | TOCs, amendment records, prefaces, charges | GEN 0.x, GEN 4.x, `*0.6`, ENR 0.6 |
| `note` *(attribute)* | Remarks/exceptions carrying conditions | "Nota:", footnotes — kept with parent |

**`data` sub-types** (the anti-numeric-distractor layer):
`frequency` · `geographic` · `schedule` · `airspace-limits` · `conversion-table`.
Tagging GEN 2.x conversion tables as `conversion-table` lets retrieval suppress the
"7000-as-altitude" noise that otherwise buries "7000-as-squawk-code".

---

## 6. Ingestion pipeline (build-time) — LOCKED

Offline CLI (not a service). Stages:

1. **Provenance bootstrap** — from manifest (AIP), filename + FRBR/`urn:nir` (AKN), or
   filename/title (legislation PDFs).
2. **Parse** —
   - Text PDFs: **Docling**, column-aware, keep Italian; preserve tables; capture per-page
     AIRAC date from header.
   - Chart pages: detected **per-page** (chars/page below ~200 + presence of image objects);
     extract whatever vector text exists, tag `chart`, otherwise *punt* (link the user to
     the chart). **No OCR / no VLM.**
   - AKN XML: **cobalt** + lxml → article text + `eId` + `urn`.
   - Legislation PDFs: Docling.
3. **Role-tag** — deterministic from section codes + within-document detection
   (note/definition/`FREQ`-column) + entity-specific-vs-agnostic flag. *Pure deterministic
   for v1*; an optional one-time **offline** LLM pass may be added later only if the build
   report shows too many ambiguous cases.
4. **Chunk** — subsection-level (AD 2.2, ENR 1.1.1, AKN article/comma). Tables kept whole;
   notes bound to parent; **stable chunk_id** (e.g. `page_id + subsection + index`) for
   idempotent replace.
5. **Embed + index** — BGE-M3 dense vectors + BM25 sparse → **LanceDB single store**
   (vectors + its built-in full-text search for the sparse leg; validate that its tokenizer
   preserves exact tokens like `LIRF`, `7000`, `126.300`; fall back to a dedicated BM25 only
   if needed).
6. **Build report** — per-file status (parsed / chart-punt / failed) + role distribution.
   This is the **validation surface** for the domain-expert user, because ingest-time
   tagging errors are baked in until re-ingest.

**Incremental AIRAC update** (`update` CLI): diff new manifest `content_hash` per `page_id`
against the index — unchanged → skip; changed/new → re-process and **replace by chunk_id**;
removed → delete. Only the delta re-embeds (minutes, not the multi-hour first build).
Legislation refreshed manually/rarely.

---

## 7. Retrieval & ranking engine — LOCKED

Five stages; each defeats a failure the others cannot:

1. **Candidate generation (hybrid).** Dense kNN (BGE-M3) ∪ sparse BM25, fused with
   **Reciprocal Rank Fusion (RRF)** (rank-based, no score calibration) → ~top 100. Sparse
   is essential for exact tokens (ICAO codes, frequencies, "7000") that dense embeddings
   blur.
2. **Metadata gate.** Apply provenance × role:
   - **HARD filter:** `entity` (resolved entity **OR** entity-agnostic — see note), AIRAC
     version, exclude `reference`.
   - **SOFT boost:** target roles ↑.
   - **Strong negative boost:** `conversion-table` ↓↓.
   → ~top 50.
3. **Rerank.** Cross-encoder **bge-reranker-v2-m3** reads (query ⊗ chunk) together; final
   score = reranker score + small metadata prior → ~top 6–8. Cap candidates (~30–50) to
   bound CPU latency.
4. **Abstention gate** (see §8) + group cross-corpus duplicates as "operational statement
   + regulatory basis".
5. **Synthesis** (see §9).

**Hard vs soft principle:** hard-filter categorical/verifiable signals; soft-boost inferred
ones. Hard-filter only where a wrong inclusion is a safety problem or the category is
certain. *Entity-agnostic escape hatch:* the entity filter is "entity = X **OR**
entity-agnostic", not "entity = X" — general rules (no airport) must survive an
airport-scoped query.

**Optional sparse-leg query expansion:** expand the BM25 query with intra-Italian jargon
synonyms (transponder ↔ SSR ↔ codice di conspicuità); never the dense leg.

---

## 8. Router — LOCKED

LLM-free, deterministic, in the hot path. Produces the engine's input contract.

**Asymmetric accuracy requirement:** entity feeds a *hard filter* → must be reliable and
**block-and-ask when in doubt** (never guess). Roles feed a *soft boost* → cheap and
approximate is fine.

- **(A) Entity resolution.** Gazetteer **auto-derived at ingest** from AD/ENR filenames
  (airports ICAO+name; FIR/CTR/TMA; `LI-P/R/D` areas) + a small **curated alias table**
  (Fiumicino→LIRF, Caselle→LIMF, Orio→LIME…) + fuzzy match for typos. Outcomes: 1 match →
  resolve; **>1 → block & clarify** ("Milano" = Linate/Malpensa/Bergamo + TMA + FIR);
  0 matches → fatal for lookups (ask "quale aeroporto?"), fine for general-rule questions.
  Airport-vs-airspace via keywords (TMA/CTR/FIR/zona). **Entity sets** supported
  ("differenza tra CTR di Linate e Malpensa").
- **(B) Intent → roles** via Italian cue patterns ("che frequenza"→`frequency`,
  "devo/posso/è obbligatorio"→`rule`, "come"→`procedure`, "cosa significa"→`definition`,
  "limiti di [airspace]"→`airspace`, "zona vietata"→`warning`). On **uncertainty → broad
  boost** (rule+procedure+data), never an LLM call. **Conditions** ("non in contatto",
  "di notte") are kept in the query untouched — the reranker/LLM use them.
- **(C) Jargon expansion** on the BM25 leg only. Plus a cheap out-of-scope gate.

**Output contract:** `{ target_roles, entity_filter, expansions }` → engine, **or**
`{ clarify, candidates }` → ask user.

---

## 9. Abstention model — LOCKED

Abstention is a set of **gates** at different stages plus a **three-tier output** (not
binary): for a findability tool, a hedged pointer beats a flat "not found".

**Gates (in order):** router off-domain → refuse; router entity missing/ambiguous →
clarify (before retrieval); post-rerank score gate; post-LLM answer-presence + citation
integrity.

**Three tiers via two reranker thresholds:**
- `score ≥ τ_high` → **CONFIDENT** (answer + citations).
- `τ_low ≤ score < τ_high` → **HEDGED** ("Non sono certo, ma la fonte più pertinente è… ",
  verify via link).
- `score < τ_low` → **HARD ABSTAIN** ("Non ho trovato…").

**Division of labor:** the **reranker** judges evidence-sufficiency (numeric, *before* the
LLM — do not ask the LLM "are these chunks enough?"); the **LLM** judges answer-presence
within already-relevant chunks (the "topical but doesn't actually state it" / chart-only
case).

**Thresholds are NOT hardcoded.** They are calibrated empirically after embedder/reranker
are fixed, using a small (~20–30) validation set of answerable / partial / out-of-corpus
questions; set `τ_low` in the score valley, biased slightly toward recall/hedging (the user
always cross-checks).

Per-claim / partial abstention is the split-answer flow. A citation-integrity guard strips
any citation that doesn't map to a retrieved chunk.

---

## 10. Synthesis contract + integrity guards — LOCKED

Generation is **slot-filling under constrained decoding** (Ollama `format` / JSON-schema,
or llama.cpp GBNF grammar — both verified to support this), *not* free-text + regex. This
makes the integrity guards trivial structural checks rather than fragile parsing.

**Output schema (the model is constrained to emit this):**
```json
{
  "status": "answered | partial | abstain",
  "claims": [
    {
      "text": "…one Italian assertion…",
      "citations": ["ENR-1.6#3", "SERA#6005"],
      "verbatim_values": ["7000"]
    }
  ],
  "gaps": [
    {"description": "…", "pointer": "ENR-6.3", "reason": "chart"}
  ]
}
```

**Chunks fed to the model** are tagged `[id | section | role] «text»`, top ~5 reranked only.

**System prompt rules (Italian):** use only provided documents; cite every claim by chunk
id; copy numbers/codes/frequencies **alla lettera** (no recompute, round, or reformulate);
`abstain` if absent, `partial` + `gaps` if partial; answer in Italian, concise.

**Deterministic post-processing:**
1. **Citation integrity** — every cited id must be in the provided set, else drop the claim.
2. **Number integrity** — every `verbatim_value`, *normalized* (strip spaces, unify decimal
   separator), must appear in at least one of that claim's cited chunks (display the
   source's exact form). Failure → strip the value. This also catches *derived* numbers,
   which won't appear verbatim in any source. **Numbers are the answers in this corpus, so
   this is the highest-value guard.**
3. **Tier decision** — combine LLM `status` × reranker tier → confident / hedged / abstain;
   if the guards gut the answer, fall back to a hedged pointer (show the best source,
   assert nothing).
4. **Render** to the presentation layer (§11).

**One compact few-shot** demonstrates the `partial` + `gaps` case (the trickiest for a 4B
model); the grammar handles the rest.

---

## 11. Answer & citation presentation — LOCKED

Layout (mocked):
```
┌ Dati AIP: ciclo AIRAC A06-26 (eff. 11 GIU 2026)
│ ⚠ Strumento di consultazione — verificare sempre la fonte ufficiale
├─────────────────────────────────────────────────────────────────────
│ R: <answer with inline [1][2] markers; split-answer flags chart-only gaps>
│ Fonti
│ [1] AIP Italia · ENR 1.6 — Procedure servizi di sorveglianza  [regola]
│     AIRAC eff. 17 APR 2025
│     «…snippet…»            ↪ Apri ENR 1.6, pag. 2   · Vedi su ENAV ↗
│ [2] Reg. UE 923/2012 (SERA) · SERA.6005             [base normativa]
│     «…snippet…»            ↪ Apri SERA, pag. 41     · Vedi su EUR-Lex ↗
└─────────────────────────────────────────────────────────────────────
```

- **Inline `[n]` markers** in the synthesis map to numbered **source cards** beneath, each
  with role tag, per-page AIRAC date, snippet text, and deep link.
- **Three tiers + clarify** render distinctly (hedged prefix; "non trovato"; clickable
  entity chips for clarify).
- **Currency + disclaimer banner** is always shown.
- **Cross-corpus grouping** renders as "operativo [1] + base normativa [2]".

**Deep-link strategy:**
| Source type | Primary (local, offline) | Secondary |
|---|---|---|
| AIP PDF | app serves file `#page=N` | ENAV `source_url` |
| Legislation PDF (SERA, ENAC) | app serves file `#page=N` | EUR-Lex / ENAC |
| Legislation **AKN XML** | snippet text extracted via cobalt (offline) | **normattiva.it** link built from `urn:nir`/ELI alias, article-anchored where the URN scheme allows |

Each AIP PDF *is* a single section, so a file link already lands on the right section;
`#page=N` refines within it. **Snippet highlighting is deferred** to a later polish pass.

---

## 12. Models & hardware — LOCKED

Hardware: 16 cores / 32 GB RAM, CPU-only. All three models stay resident comfortably. CPU
inference is **memory-bandwidth bound** (~8–12 threads optimal; ~15–25 tok/s for 7–8B Q4,
~30+ for 4B).

| Slot | Choice | Notes |
|---|---|---|
| Embedder (dense) | **BGE-M3** (Apache 2.0) | best multilingual hybrid; heavy cost at ingest only |
| Sparse | **BM25** | exact tokens (ICAO/freq/"7000") |
| Reranker | **bge-reranker-v2-m3** (Apache 2.0) | cap candidates to bound CPU latency |
| Generation LLM | **Qwen3 4B (default)**, A/B **Minerva 7B**; escalate to 8B only if needed | thin/extractive → instruction-following discipline matters more than fluency |

Served via **Ollama**. Stream-free but cards-first (see below). Minerva-7B-instruct has an
official GGUF (Sapienza) runnable in Ollama/llama.cpp; A/B against Qwen3 4B is cheap since
the user validates manually.

**Perceived-latency strategy:** render source cards immediately from retrieval (fast), fill
in the synthesis when the constrained JSON completes. Latency is retrieval-bound, not
generation-bound, and it sidesteps the "JSON can't stream" problem.

---

## 13. Distractor classes (why the engine is shaped as it is)

| Class | Example (from the corpus) | Defense |
|---|---|---|
| Numeric homonyms | "7000" = squawk vs 7000 ft vs 7000 kg | `data` sub-type tagging + reranker |
| Polysemy | "avvicinamento" = phase vs APP unit; "monitoring" = radio vs flight-path | reranker + LLM context |
| Definition vs operational | SERA Art. 2 definitions over-match term queries | `definition` role deprioritized |
| **Condition / negation** | ENR 1.6 VFR+transponder passages with opposite conditions | cross-encoder + LLM — *irreducible*; citations are the safety net |

Class 4 (condition/negation) cannot be fully solved by retrieval; the citation-first design
is precisely the mitigation.

---

## 14. Open items

### 14.1 Optional remaining refinement
- **C — conflicting / superseding sources.** The cross-corpus "corroboration" framing
  assumes AIP, SERA, ENAC, and *Regole dell'aria* agree. They can disagree (EU vs national
  derogation; a 2012 regulation vs a newer AIRAC; general vs specific). At minimum, **flag
  potential conflicts** rather than always framing as agreement; ideally encode a light
  source-hierarchy / recency prior. Not yet designed.

### 14.2 Validate-in-slice (cannot be resolved on paper)
- **Column-extraction fidelity** — the Italian-only index assumes Docling cleanly separates
  the row-by-row IT/EN columns. Must be verified on a real `AD 2.x` page.
- **Sub-document role-tagging accuracy** — section-level tagging is safe; within-document
  heuristics (note/definition/`FREQ`-column) are unproven on real layout.
- **End-to-end CPU latency** — reranking 50 cross-encoder pairs + LLM prompt-eval over
  several chunks is estimated, not measured.

### 14.3 Minor build-time choices
- Web UI: leaning **Streamlit**.
- Framework: **LlamaIndex** vs hand-rolled — decide at build time.
- Curated alias table and the ~20–30 question validation set need seeding (domain input).

---

## 15. Recommended first build step

A **thin vertical slice**: ingest a handful of real files (a table-heavy `AD 2` aerodrome
page, one `ENR` section, one AKN XML) → role-tag → index → answer one Italian question
end-to-end with working citations, deep links, and AIRAC date. This de-risks the three
validate-in-slice unknowns (especially column extraction and CPU latency) before scaling to
the full ~900-file corpus.

---

## Glossary
- **AIP** — Aeronautical Information Publication.
- **AIRAC** — Aeronautical Information Regulation And Control; fixed 28-day update cycle.
- **ENAV** — Italian air navigation service provider (publishes the AIP).
- **VDS** — *Volo da Diporto o Sportivo* (recreational/sport flight).
- **GEN / ENR / AD** — the three AIP parts: General / En-route / Aerodromes.
- **Akoma Ntoso (AKN)** — OASIS LegalDocML XML standard for legal documents.
- **FRBR / ELI / urn:nir** — identifier schemes for legal resources (used by Normattiva).
- **SERA** — Standardised European Rules of the Air (EU Reg. 923/2012).
- **RRF** — Reciprocal Rank Fusion.
- **GBNF** — grammar format used by llama.cpp for constrained decoding.
