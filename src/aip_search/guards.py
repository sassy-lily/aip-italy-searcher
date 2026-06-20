"""Deterministic integrity guards over the LLM's structured output (ARCHITECTURE.md §10).

Because synthesis is constrained JSON, these are simple structural checks, not NLP:
  1. Citation integrity — every cited id must be a retrieved chunk, else drop the claim.
  2. Number integrity   — every number (declared verbatim, or appearing in the claim text)
     must appear verbatim in a cited chunk, else flag it. Catches mangled or derived
     numbers — the highest-value guard, since numbers ARE the answers in this corpus.
"""
from __future__ import annotations

import re

from .retrieve import Result
from .synth import Answer, Claim

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _norm(s: str) -> str:
    """Normalize for comparison: drop spaces, unify decimal separator."""
    return re.sub(r"\s+", "", s).replace(",", ".")


_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def verify(answer: Answer, results: list[Result]) -> tuple[Answer, list[str]]:
    by_id = {r.meta["id"]: r for r in results}
    flags: list[str] = []
    kept: list[Claim] = []

    for c in answer.claims:
        cites = [cid for cid in c.citations if cid in by_id]
        if len(cites) != len(c.citations):
            flags.append(f"citazione inesistente rimossa ({c.text[:48]}…)")
        if not cites:
            flags.append(f"claim senza citazioni valide scartato ({c.text[:48]}…)")
            continue

        cited_norm = _norm(" ".join(by_id[cid].text for cid in cites))
        # Reference numbers (law/section ids) are not data — don't police them.
        ref_nums = set()
        for cid in cites:
            ref_nums |= set(_NUM_RE.findall(by_id[cid].meta.get("section_code", "")))

        # Declared verbatim values must appear in a cited chunk.
        good_vals = []
        for v in c.verbatim_values:
            if _norm(v) in cited_norm:
                good_vals.append(v)
            else:
                flags.append(f"valore non verificato rimosso: '{v}'")

        # Numbers in the claim text must be grounded — but skip years and
        # law/section reference numbers (the guard targets DATA, not citations).
        for n in _NUM_RE.findall(c.text):
            if len(n) < 2 or _YEAR_RE.match(n) or n in ref_nums:
                continue
            if _norm(n) not in cited_norm:
                flags.append(f"numero non verificato nel testo: '{n}' ({c.text[:40]}…)")

        kept.append(Claim(text=c.text, citations=cites, verbatim_values=good_vals))

    answer.claims = kept
    return answer, flags
