"""Typer CLI for the vertical slice.

Phase 1 commands (`ingest`, `query`) need no LLM. `ask` (synthesis) arrives in Phase 2
once Ollama is installed.
"""
from __future__ import annotations

from collections import Counter

import typer
from rich.console import Console

from .answer import TAU_HIGH, TAU_LOW, answer
from .index import build_index
from .ingest import ingest_corpus
from .retrieve import Retriever
from .router import Route, route

app = typer.Typer(add_completion=False, help="AIP-Search — local Italian AIP/VDS lookup (slice).")
console = Console()

@app.command()
def ingest(full: bool = typer.Option(False, "--full", help="Ingest the whole corpus (else 3-file slice).")) -> None:
    """Parse, role-tag, chunk, embed, and build the LanceDB index. Prints a build report."""
    chunks, report = ingest_corpus(full=full)
    n = build_index(chunks)
    console.print(
        f"[green]Indexed {n} chunks[/green] from {report['aip_files']} AIP + "
        f"{report['vds_files']} VDS files."
    )
    dist = Counter(c.role.value for c in chunks)
    console.print("roles: " + ", ".join(f"{k}={v}" for k, v in dist.most_common()))
    if report["failed"]:
        console.print(f"[yellow]{len(report['failed'])} file(s) failed to parse:[/yellow]")
        for name, err in report["failed"][:25]:
            console.print(f"  · {name}: {err}")


def _route_or_stop(rt: Route) -> bool:
    """Render a clarify/abstain decision; return True if the caller should stop."""
    if rt.kind == "abstain":
        console.print(f"[red]{rt.message}[/red]")
        return True
    if rt.kind == "clarify":
        console.print(f"[yellow]{rt.message}[/yellow]")
        for ic, name in rt.candidates:
            console.print(f"  • {ic} — {name}")
        return True
    return False


@app.command()
def query(question: str, k: int = 5) -> None:
    """Retrieve + rerank for an Italian question (sources only, no synthesis)."""
    rt = route(question)
    if _route_or_stop(rt):
        return
    if rt.entity_filter:
        console.print(f"[dim]entity: {', '.join(sorted(rt.entity_filter))}[/dim]")
    r = Retriever()
    res = r.search(question, entity_filter=rt.entity_filter, roles=rt.roles, subs=rt.subs, k=k)
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


def _render_sources(sources, label: str = "Fonti") -> None:
    console.print(f"\n[bold]{label}:[/bold]")
    for n, meta, text in sources:
        role = meta["role"] + (f"/{meta['data_subtype']}" if meta["data_subtype"] else "")
        ent = f" · {meta['entity']}" if meta["entity"] else ""
        console.print(
            f"  [bold][{n}][/bold] {meta['section_code']} · {role}{ent}"
            f" · AIRAC {meta['airac_effective_date'] or 'n/d'}"
        )
        console.print(f"      {text[:160].replace(chr(10), ' ')}")
        if meta["source_url"]:
            console.print(f"      [blue]{meta['source_url']}[/blue]")


@app.command()
def ask(question: str, k: int = 5) -> None:
    """Answer an Italian question with a synthesized, cited response."""
    res = answer(question, Retriever(), k=k)
    if res.kind == "abstain":
        console.print(f"[red]{res.message}[/red]")
        return
    if res.kind == "clarify":
        console.print(f"[yellow]{res.message}[/yellow]")
        for ic, name in res.candidates:
            console.print(f"  • {ic} — {name}")
        return

    console.print(
        f"[dim]Dati AIP: ciclo AIRAC {res.cycle or 'n/d'} · "
        f"⚠ strumento di consultazione, non per uso operativo[/dim]"
    )
    if res.tier == "abstain":
        console.print(f"[red]tier: abstain[/red] — {res.message}")
        _render_sources(res.sources, label="Fonti consultate")
        return

    color = {"confident": "green", "hedged": "yellow"}[res.tier]
    console.print(f"[{color}]tier: {res.tier}[/{color}]\n")
    console.print("[bold]Risposta:[/bold]")
    if res.tier == "hedged":
        console.print("[yellow]⚠ Non sono del tutto certo; verifica nelle fonti citate.[/yellow]")
    for text, marks in res.claims:
        console.print(f"  {text} {''.join(f'[{n}]' for n in marks)}")
    for desc, ptr, reason in res.gaps:
        p = f" → vedi {ptr}" if ptr else ""
        console.print(f"  [dim]lacuna: {desc}{p} ({reason or 'n/d'})[/dim]")

    _render_sources(res.sources)

    if res.flags:
        console.print("\n[dim]Note di verifica (guardrail):[/dim]")
        for f in res.flags:
            console.print(f"  [dim]· {f}[/dim]")


def main() -> None:
    app()
