"""End-to-end answer pipeline, shared by the CLI and the Streamlit UI.

route → search → synthesize → integrity guards → tier + citation numbering, returned as a
render-agnostic AnswerResult so both front-ends render identical logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .guards import verify
from .retrieve import Retriever
from .router import Route, role_hints, route
from .synth import synthesize

# Abstention thresholds — calibrated for bge-reranker-v2-m3 on the full corpus, router-aware,
# via scripts/calibrate_thresholds.py (off-domain ≤0.20, answerable ≥0.54). Foreign airports
# are abstained by the router; poorly-retrieved answerables fall below TAU_LOW and abstain.
TAU_HIGH = 0.90  # ≥ → confident
TAU_LOW = 0.35   # < → abstain


@dataclass
class AnswerResult:
    kind: str  # "answer" | "clarify" | "abstain"
    message: str | None = None
    candidates: list[tuple[str, str]] = field(default_factory=list)  # clarify: (icao, name)
    tier: str | None = None  # "confident" | "hedged" | "abstain"
    claims: list[tuple[str, list[int]]] = field(default_factory=list)  # (text, citation numbers)
    gaps: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    sources: list[tuple[int, dict, str]] = field(default_factory=list)  # (n, meta, text)
    flags: list[str] = field(default_factory=list)
    cycle: str | None = None


def _not_found(res, cycle) -> AnswerResult:
    return AnswerResult(
        "answer", tier="abstain", cycle=cycle,
        message="Le fonti recuperate non contengono la risposta.",
        sources=[(i + 1, x.meta, x.text) for i, x in enumerate(res[:3])],
    )


def answer(question: str, retriever: Retriever, force_entity: set[str] | None = None,
           k: int = 5) -> AnswerResult:
    if force_entity:
        roles, subs = role_hints(question)
        rt = Route("search", entity_filter=set(force_entity), roles=roles, subs=subs)
    else:
        rt = route(question)

    if rt.kind == "abstain":
        return AnswerResult("abstain", message=rt.message)
    if rt.kind == "clarify":
        return AnswerResult("clarify", message=rt.message, candidates=rt.candidates)

    res = retriever.search(question, entity_filter=rt.entity_filter, roles=rt.roles,
                           subs=rt.subs, k=k)
    cycle = res[0].meta.get("snapshot_cycle") if res else None
    if not res or res[0].rerank_score < TAU_LOW:
        return _not_found(res, cycle)

    top = res[0].rerank_score
    ans = synthesize(question, res)
    ans, flags = verify(ans, res)
    if ans.status == "abstain" or not ans.claims:
        return _not_found(res, cycle)

    tier = "confident" if (ans.status == "answered" and not flags and top >= TAU_HIGH) else "hedged"

    order: list[str] = []
    for c in ans.claims:
        for cid in c.citations:
            if cid not in order:
                order.append(cid)
    num = {cid: i + 1 for i, cid in enumerate(order)}
    by_id = {x.meta["id"]: x for x in res}

    claims = [(c.text, [num[cid] for cid in c.citations if cid in num]) for c in ans.claims]
    sources = [(num[cid], by_id[cid].meta, by_id[cid].text) for cid in order if cid in by_id]
    gaps = [(g.description, g.pointer, g.reason) for g in ans.gaps]
    return AnswerResult("answer", tier=tier, cycle=cycle, claims=claims, gaps=gaps,
                        sources=sources, flags=flags)
