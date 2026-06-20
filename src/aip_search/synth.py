"""Thin synthesis via Ollama with constrained-decoding JSON output (ARCHITECTURE.md §10).

The model fills a fixed schema (claims + citations + verbatim values + gaps); it never
free-writes the format. `think=False` keeps qwen3 from emitting long reasoning.
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
    citations: list[str]
    verbatim_values: list[str] = []


class Answer(BaseModel):
    status: Literal["answered", "partial", "abstain"]
    claims: list[Claim] = []
    gaps: list[Gap] = []


SYSTEM = """Sei un assistente di consultazione per l'AIP italiano e la normativa VDS.
Regole inderogabili:
- Usa SOLO i documenti forniti; non aggiungere conoscenza esterna.
- Cita OGNI affermazione con gli id dei chunk forniti (campo citations).
- Copia numeri, codici e frequenze ALLA LETTERA dai documenti nel campo verbatim_values;
  non ricalcolarli, arrotondarli o riformularli.
- Se i documenti NON contengono la risposta: status="abstain", claims vuoto.
- Se la contengono solo in parte: status="partial" ed elenca le lacune in gaps.
- Rispondi in italiano, conciso. Ogni claim è una singola affermazione."""


def _context(results: list[Result]) -> str:
    blocks = []
    for r in results:
        m = r.meta
        role = m["role"] + (f"/{m['data_subtype']}" if m["data_subtype"] else "")
        blocks.append(f'[id={m["id"]} | {m["section_code"]} | {role}] «{r.text[:800]}»')
    return "\n\n".join(blocks)


def synthesize(question: str, results: list[Result], model: str = LLM_MODEL) -> Answer:
    user = f"Domanda: {question}\n\nDocumenti forniti:\n{_context(results)}"
    resp = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
        format=Answer.model_json_schema(),
        think=False,
        options={"temperature": 0},
    )
    return Answer.model_validate_json(resp.message.content)
