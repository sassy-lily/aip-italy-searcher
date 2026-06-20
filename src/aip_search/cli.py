"""Typer CLI for the vertical slice.

Phase 1 commands (`ingest`, `query`) need no LLM. `ask` (synthesis) arrives in Phase 2
once Ollama is installed.
"""
from __future__ import annotations

import typer
from rich.console import Console

from .guards import verify
from .index import build_index
from .ingest import ingest_all
from .retrieve import Result, Retriever, detect_entity
from .synth import synthesize

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


def _print_sources(results: list[Result], label: str = "Fonti") -> None:
    console.print(f"\n[bold]{label}:[/bold]")
    for x in results:
        m = x.meta
        console.print(f"  · {m['section_code']} · {m['role']} · score {x.rerank_score:.2f}")
        if m["source_url"]:
            console.print(f"    [blue]{m['source_url']}[/blue]")


@app.command()
def ask(question: str, k: int = 5) -> None:
    """Answer an Italian question with a synthesized, cited response (Phase 2)."""
    r = Retriever()
    res = r.search(question, k=k)
    if not res:
        console.print("[red]Non ho trovato informazioni pertinenti.[/red]")
        return

    top = res[0].rerank_score
    cycle = res[0].meta.get("snapshot_cycle") or "n/d"
    console.print(
        f"[dim]Dati AIP: ciclo AIRAC {cycle} · "
        f"⚠ strumento di consultazione, non per uso operativo[/dim]"
    )

    if top < TAU_LOW:
        console.print(f"[red]Non ho trovato informazioni pertinenti (score {top:.2f}).[/red]")
        _print_sources(res[:3], label="Fonti più vicine")
        return

    ans = synthesize(question, res)
    ans, flags = verify(ans, res)

    if ans.status == "abstain" or not ans.claims:
        console.print("[red]tier: abstain[/red] — le fonti recuperate non contengono la risposta.")
        _print_sources(res[:3], label="Fonti consultate")
        return
    if ans.status == "partial" or flags or top < TAU_HIGH:
        tier, color = "hedged", "yellow"
    else:
        tier, color = "confident", "green"
    console.print(f"[{color}]tier: {tier}[/{color}]\n")

    # Assign citation numbers in order of first appearance.
    order: list[str] = []
    for c in ans.claims:
        for cid in c.citations:
            if cid not in order:
                order.append(cid)
    num = {cid: i + 1 for i, cid in enumerate(order)}
    by_id = {x.meta["id"]: x for x in res}

    console.print("[bold]Risposta:[/bold]")
    if tier == "hedged":
        console.print("[yellow]⚠ Non sono del tutto certo; verifica nelle fonti citate.[/yellow]")
    for c in ans.claims:
        marks = "".join(f"[{num[cid]}]" for cid in c.citations if cid in num)
        console.print(f"  {c.text} {marks}")
    for g in ans.gaps:
        ptr = f" → vedi {g.pointer}" if g.pointer else ""
        console.print(f"  [dim]lacuna: {g.description}{ptr} ({g.reason or 'n/d'})[/dim]")

    console.print("\n[bold]Fonti:[/bold]")
    for cid in order:
        x = by_id.get(cid)
        if not x:
            continue
        m = x.meta
        role = m["role"] + (f"/{m['data_subtype']}" if m["data_subtype"] else "")
        ent = f" · {m['entity']}" if m["entity"] else ""
        console.print(
            f"  [bold][{num[cid]}][/bold] {m['section_code']} · {role}{ent}"
            f" · AIRAC {m['airac_effective_date'] or 'n/d'}"
        )
        console.print(f"      {x.text[:160].replace(chr(10), ' ')}")
        if m["source_url"]:
            console.print(f"      [blue]{m['source_url']}[/blue]")

    if flags:
        console.print("\n[dim]Note di verifica (guardrail):[/dim]")
        for f in flags:
            console.print(f"  [dim]· {f}[/dim]")


def main() -> None:
    app()
