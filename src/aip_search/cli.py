"""Typer CLI for the vertical slice.

Phase 1 commands (`ingest`, `query`) need no LLM. `ask` (synthesis) arrives in Phase 2
once Ollama is installed.
"""
from __future__ import annotations

from collections import Counter

import typer
from rich.console import Console

from .guards import verify
from .index import build_index
from .ingest import ingest_corpus
from .retrieve import Result, Retriever
from .router import Route, route
from .synth import synthesize

app = typer.Typer(add_completion=False, help="AIP-Search — local Italian AIP/VDS lookup (slice).")
console = Console()

# Abstention thresholds, calibrated for bge-reranker-v2-m3 on the FULL corpus via
# scripts/calibrate_thresholds.py (router-aware, ≥10 questions/kind). Foreign-airport queries
# are abstained by the router now; the SCORE GATE handles the rest — off-domain scored ≤0.20,
# well-retrieved answers ≥0.54. Poorly-retrieved answerables fall below TAU_LOW and abstain,
# which is correct. Re-run after any reranker or corpus change; the scale is model-specific.
TAU_HIGH = 0.90  # ≥ → confident
TAU_LOW = 0.35   # < → abstain; between → hedged


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
    rt = route(question)
    if _route_or_stop(rt):
        return
    r = Retriever()
    res = r.search(question, entity_filter=rt.entity_filter, roles=rt.roles, subs=rt.subs, k=k)
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
