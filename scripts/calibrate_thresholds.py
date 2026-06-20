"""Calibrate the abstention thresholds for the active reranker (ARCHITECTURE.md §9).

Runs a small labeled question set through the retriever, records the top reranker score
per question, and reports the answerable/unanswerable distributions so τ_low (abstain
boundary) and τ_high (confident boundary) can be set in the gap. Re-run after any reranker
or corpus change — the score scale is model-specific.

    uv run python scripts/calibrate_thresholds.py
"""
from __future__ import annotations

import statistics

from aip_search.retrieve import Retriever

# Labelled relative to the FULL corpus (entire Italian AIP + VDS legislation).
# Negatives must be genuinely outside it: foreign airports (aviation-topical but absent
# from the Italian AIP) or off-domain. Italian airports/airspace are all in-corpus.
QUESTIONS: list[tuple[str, str]] = [
    # Answerable (span the corpus: aerodrome data, ENR rules/airspace/warnings, legislation)
    ("Quale codice transponder si seleziona per un'emergenza?", "answerable"),
    ("Cos'è il volo da diporto o sportivo?", "answerable"),
    ("Quali sono i requisiti del transponder SSR nello spazio aereo italiano?", "answerable"),
    ("Come avviene la distribuzione dei codici SSR Modo A in Italia?", "answerable"),
    ("Quale codice transponder in VFR non in contatto con l'ATC?", "answerable"),
    ("Qual è la frequenza TWR di LIRF?", "answerable"),
    ("Quali servizi di comunicazione ATS sono disponibili a Crotone?", "answerable"),
    ("Quali sono i limiti verticali della TMA di Milano?", "answerable"),
    ("Dove si trovano le zone vietate o proibite al volo in Italia?", "answerable"),
    ("Quali sanzioni sono previste per il volo da diporto o sportivo?", "answerable"),
    # Unanswerable — foreign airports (aviation but not in the Italian AIP)
    ("Qual è la frequenza della torre dell'aeroporto di Londra Heathrow?", "unanswerable"),
    ("Quali sono le procedure di avvicinamento per l'aeroporto JFK di New York?", "unanswerable"),
    ("Qual è l'elevazione dell'aeroporto di Parigi Charles de Gaulle?", "unanswerable"),
    # Unanswerable — off-domain
    ("Come si prepara la pasta alla carbonara?", "unanswerable"),
    ("Qual è la capitale dell'Australia?", "unanswerable"),
    ("Chi ha vinto il campionato di calcio nel 1982?", "unanswerable"),
]


def main() -> None:
    r = Retriever()
    rows = []
    for q, label in QUESTIONS:
        res = r.search(q, k=1)
        top = res[0].rerank_score if res else float("-inf")
        sec = res[0].meta["section_code"] if res else "—"
        rows.append((label, top, q, sec))

    rows.sort(key=lambda x: x[1], reverse=True)
    print(f"\n{'score':>8}  {'label':<12} {'top hit':<22} question")
    print("-" * 90)
    for label, score, q, sec in rows:
        print(f"{score:8.3f}  {label:<12} {sec:<22} {q[:42]}")

    ans = sorted((s for l, s, _, _ in rows if l == "answerable"))
    una = sorted((s for l, s, _, _ in rows if l == "unanswerable"))
    print("\nanswerable  : min={:.3f} median={:.3f} max={:.3f}".format(ans[0], statistics.median(ans), ans[-1]))
    print("unanswerable: min={:.3f} median={:.3f} max={:.3f}".format(una[0], statistics.median(una), una[-1]))

    gap_top, gap_bottom = max(una), min(ans)
    if gap_bottom > gap_top:
        tau_low = round((gap_top + gap_bottom) / 2, 3)
        sep = "clean separation"
    else:
        tau_low = round(statistics.median([gap_top, gap_bottom]), 3)
        sep = f"OVERLAP (max unanswerable {gap_top:.3f} ≥ min answerable {gap_bottom:.3f})"
    tau_high = round(statistics.median(ans), 3)
    print(f"\nseparation: {sep}")
    print(f"suggested  TAU_LOW  = {tau_low}   (below → abstain)")
    print(f"suggested  TAU_HIGH = {tau_high}   (above → confident; between → hedged)")


if __name__ == "__main__":
    main()
