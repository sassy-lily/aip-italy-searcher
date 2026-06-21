"""Thin synthesis via Ollama with constrained-decoding JSON output (ARCHITECTURE.md §10).

Chunks are labelled with small integers [1..N] and the model cites by integer; we map those
back to chunk-ids before returning. Integers are far more robust for a small model to echo
exactly than long string ids (which it mangled — e.g. copying the "id=" prefix). `think=False`
keeps qwen3 from emitting long reasoning.
"""
from __future__ import annotations

from typing import Literal

import ollama
from pydantic import BaseModel

from .config import LLM_MODEL
from .retrieve import Result


class Gap(BaseModel):
    description: str
    pointer: str | None = None
    reason: str | None = None


class Claim(BaseModel):
    text: str
    citations: list[str]  # chunk-ids (mapped from the model's integer labels)
    verbatim_values: list[str] = []


class Answer(BaseModel):
    status: Literal["answered", "partial", "abstain"]
    claims: list[Claim] = []
    gaps: list[Gap] = []


# Internal schema the LLM is constrained to — citations are integer labels.
class _LClaim(BaseModel):
    text: str
    citations: list[int]
    verbatim_values: list[str] = []


class _LAnswer(BaseModel):
    status: Literal["answered", "partial", "abstain"]
    claims: list[_LClaim] = []
    gaps: list[Gap] = []


SYSTEM = """Sei un assistente di consultazione per l'AIP italiano e la normativa VDS.
Regole inderogabili:
- Usa SOLO i documenti forniti; non aggiungere conoscenza esterna.
- Cita OGNI affermazione con i NUMERI dei documenti pertinenti nel campo citations
  (es. citations: [1, 3]). Usa solo i numeri elencati tra parentesi quadre.
- Scegli il documento che risponde ESATTAMENTE alle condizioni della domanda.
- Copia numeri, codici e frequenze ALLA LETTERA dai documenti nel campo verbatim_values;
  non ricalcolarli, arrotondarli o riformularli.
- Se i documenti NON contengono la risposta: status="abstain", claims vuoto.
- Se la contengono solo in parte: status="partial" ed elenca le lacune in gaps.
- Rispondi in italiano, conciso. Ogni claim è una singola affermazione."""


def _context(results: list[Result]) -> str:
    blocks = []
    for i, r in enumerate(results, 1):
        m = r.meta
        role = m["role"] + (f"/{m['data_subtype']}" if m["data_subtype"] else "")
        blocks.append(f"[{i}] ({m['section_code']} | {role}) «{r.text[:800]}»")
    return "\n\n".join(blocks)


def synthesize(question: str, results: list[Result], model: str = LLM_MODEL) -> Answer:
    user = f"Domanda: {question}\n\nDocumenti forniti:\n{_context(results)}"
    resp = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        format=_LAnswer.model_json_schema(),
        think=False,
        options={"temperature": 0},
    )
    la = _LAnswer.model_validate_json(resp.message.content)

    # Map integer labels [1..N] back to chunk-ids; drop out-of-range labels.
    id_of = {i: r.meta["id"] for i, r in enumerate(results, 1)}
    claims = [
        Claim(
            text=c.text,
            citations=[id_of[k] for k in c.citations if k in id_of],
            verbatim_values=c.verbatim_values,
        )
        for c in la.claims
    ]
    return Answer(status=la.status, claims=claims, gaps=la.gaps)
