"""Calibrate the abstention thresholds for the active reranker (ARCHITECTURE.md §9).

Router-aware: each question is routed first. Questions the router resolves to clarify/abstain
are reported separately (the router, not the score gate, handles them). The score-gate
thresholds (τ_low/τ_high) are derived only from questions that route to *search* — the inputs
the gate is actually responsible for. Re-run after any reranker or corpus change; the score
scale is model-specific.

    uv run python scripts/calibrate_thresholds.py
"""
from __future__ import annotations

import statistics

from aip_search.retrieve import Retriever
from aip_search.router import route

# ≥10 per kind. Negatives that must be caught by the SCORE GATE are off-domain questions
# (no airport trigger → they route to open search). Foreign-airport negatives are included
# too, but the ROUTER abstains them — they're reported, not score-calibrated.
QUESTIONS: list[tuple[str, str]] = [
    # --- Answerable (route to search: open or resolved) ---
    ("Quale codice transponder si seleziona per un'emergenza?", "answerable"),
    ("Cos'è il volo da diporto o sportivo?", "answerable"),
    ("Quali sono i requisiti del transponder SSR nello spazio aereo italiano?", "answerable"),
    ("Come avviene la distribuzione dei codici SSR Modo A in Italia?", "answerable"),
    ("Quale codice transponder si usa in VFR non in contatto con l'ATC?", "answerable"),
    ("Qual è la frequenza TWR di LIRF?", "answerable"),
    ("Quali sono i limiti verticali della TMA di Milano?", "answerable"),
    ("Dove si trovano le zone vietate o proibite al volo in Italia?", "answerable"),
    ("Quali sanzioni sono previste per il volo da diporto o sportivo?", "answerable"),
    ("Quali servizi di comunicazione ATS sono disponibili a Crotone?", "answerable"),
    ("Quali sono le caratteristiche fisiche delle piste di Pisa?", "answerable"),
    ("Come si seleziona l'identificazione dell'aeromobile con il transponder Modo S?", "answerable"),
    # --- Unanswerable, off-domain (route to open search → SCORE GATE must catch) ---
    ("Come si prepara la pasta alla carbonara?", "unanswerable"),
    ("Qual è la capitale dell'Australia?", "unanswerable"),
    ("Chi ha vinto il campionato mondiale di calcio nel 1982?", "unanswerable"),
    ("Quanto costa un abbonamento mensile della metropolitana?", "unanswerable"),
    ("Come si cura un raffreddore?", "unanswerable"),
    ("Qual è la trama dell'Odissea di Omero?", "unanswerable"),
    ("Quanto dista la Luna dalla Terra?", "unanswerable"),
    ("Come si pota un ulivo?", "unanswerable"),
    ("Chi ha dipinto la Gioconda?", "unanswerable"),
    ("Qual è il prezzo dell'oro oggi?", "unanswerable"),
    ("Quali sono gli ingredienti del tiramisù?", "unanswerable"),
    ("Come si addestra un cane da guardia?", "unanswerable"),
    # --- Unanswerable, foreign airports (ROUTER abstains — not score-calibrated) ---
    ("Quali sono le procedure di avvicinamento per l'aeroporto JFK di New York?", "unanswerable"),
    ("Qual è la frequenza della torre dell'aeroporto di Londra Heathrow?", "unanswerable"),
    ("Qual è l'elevazione dell'aeroporto di Parigi Charles de Gaulle?", "unanswerable"),
]


def main() -> None:
    r = Retriever()
    searched, routed = [], []  # searched: (label, score, hit, q); routed: (label, kind, q)
    for q, label in QUESTIONS:
        rt = route(q)
        if rt.kind != "search":
            routed.append((label, rt.kind, q))
            continue
        res = r.search(q, entity_filter=rt.entity_filter, roles=rt.roles, subs=rt.subs, k=1)
        top = res[0].rerank_score if res else 0.0
        hit = res[0].meta["section_code"] if res else "—"
        searched.append((label, top, hit, q))

    print("\n--- handled by ROUTER (not score-gated) ---")
    for label, kind, q in routed:
        print(f"  {kind:8} [{label:<12}] {q[:60]}")

    searched.sort(key=lambda x: x[1], reverse=True)
    print(f"\n--- score-gated (routed to search) ---\n{'score':>8}  {'label':<12} {'hit':<18} question")
    print("-" * 92)
    for label, score, hit, q in searched:
        print(f"{score:8.3f}  {label:<12} {hit:<18} {q[:42]}")

    ans = sorted(s for l, s, _, _ in searched if l == "answerable")
    una = sorted(s for l, s, _, _ in searched if l == "unanswerable")
    print(f"\nanswerable  (n={len(ans)}): min={ans[0]:.3f} median={statistics.median(ans):.3f} max={ans[-1]:.3f}")
    print(f"unanswerable(n={len(una)}): min={una[0]:.3f} median={statistics.median(una):.3f} max={una[-1]:.3f}")

    gap_top, gap_bottom = max(una), min(ans)
    if gap_bottom > gap_top:
        print(f"\nclean separation. suggested TAU_LOW = {round((gap_top + gap_bottom) / 2, 3)}")
    else:
        print(f"\nOVERLAP: max unanswerable {gap_top:.3f} ≥ min answerable {gap_bottom:.3f}")
        print(f"  → TAU_LOW above the off-domain cluster, below the answerable min ({gap_bottom:.3f}).")
    print(f"suggested TAU_HIGH = {round(statistics.median(ans), 3)} (confident; between → hedged)")


if __name__ == "__main__":
    main()
