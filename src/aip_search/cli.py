"""Typer CLI for the vertical slice.

Phase 1 commands (`ingest`, `query`) need no LLM. `ask` (synthesis) arrives in Phase 2
once Ollama is installed.
"""
from __future__ import annotations

import typer
from rich.console import Console

from .index import build_index
from .ingest import ingest_all
from .retrieve import Retriever, detect_entity

app = typer.Typer(add_completion=False, help="AIP-Search — local Italian AIP/VDS lookup (slice).")
console = Console()

# PLACEHOLDER thresholds on jina-reranker logits — to be calibrated empirically
# on a real question set (ARCHITECTURE.md §9). Not production values.
TAU_HIGH = 0.0
TAU_LOW = -3.0


@app.command()
def ingest() -> None:
    """Parse the slice files, role-tag, embed, and build the LanceDB index."""
    n = build_index(ingest_all())
    console.print(f"[green]Indexed {n} chunks.[/green]")


@app.command()
def query(question: str, k: int = 5) -> None:
    """Retrieve + rerank for an Italian question (sources only, no synthesis)."""
    r = Retriever()
    res = r.search(question, k=k)
    ent = detect_entity(question)
    if ent:
        console.print(f"[dim]entity resolved: {ent}[/dim]")
    if not res:
        console.print("[yellow]Nessun risultato pertinente.[/yellow]")
        return

    top = res[0].rerank_score
    tier = "confident" if top >= TAU_HIGH else "hedged" if top >= TAU_LOW else "abstain"
    color = {"confident": "green", "hedged": "yellow", "abstain": "red"}[tier]
    console.print(f"[{color}]tier: {tier}[/{color}] (top rerank score {top:.2f})\n")

    for i, x in enumerate(res, 1):
        m = x.meta
        sub = f"/{m['data_subtype']}" if m["data_subtype"] else ""
        ent2 = f" · {m['entity']}" if m["entity"] else ""
        airac = m["airac_effective_date"] or "n/d"
        console.print(
            f"[bold][{i}][/bold] {m['section_code']} · {m['role']}{sub}{ent2}"
            f" · AIRAC {airac} · score {x.rerank_score:.2f}"
        )
        console.print(f"    {x.text[:200].replace(chr(10), ' ')}")
        if m["source_url"]:
            console.print(f"    [blue]{m['source_url']}[/blue]")
    console.print("\n[dim](Phase 1 — retrieval only. Synthesis arrives in Phase 2 with Ollama.)[/dim]")


def main() -> None:
    app()
