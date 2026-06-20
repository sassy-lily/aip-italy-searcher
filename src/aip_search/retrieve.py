"""Retrieval engine: hybrid (dense ∪ BM25) → RRF → metadata gate → cross-encoder rerank.

Slice scope: a light deterministic router stub (entity detect + role hints) feeds the
gate. The full router (ARCHITECTURE.md §8) is a later thread.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from rank_bm25 import BM25Okapi

from .config import RERANK_MODEL
from .index import embed_query, open_table

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")
_ICAO_RE = re.compile(r"\bLI[A-Z]{2}\b")

# Slice stub for the gazetteer/alias layer (the real one is auto-derived at ingest).
_ALIAS = {"crotone": "LIBC"}

# Italian intent cues → roles/data-subtypes to soft-boost.
_ROLE_CUES = {
    "frequenz": ("data", "frequency"),
    "freq": ("data", "frequency"),
    "transponder": ("rule", None),
    "ssr": ("rule", None),
    "squawk": ("rule", None),
    "codice": ("rule", None),
    "come": ("procedure", None),
    "procedura": ("procedure", None),
    "cosa significa": ("definition", None),
    "definizione": ("definition", None),
    "limiti": ("airspace", None),
    "spazio aereo": ("airspace", None),
    "vietat": ("warning", None),
    "proibit": ("warning", None),
}


def _tok(s: str) -> list[str]:
    return _TOKEN_RE.findall(s.lower())


def detect_entity(query: str) -> str | None:
    m = _ICAO_RE.search(query.upper())
    if m:
        return m.group(0)
    low = query.lower()
    for name, icao in _ALIAS.items():
        if name in low:
            return icao
    return None


def role_hints(query: str) -> tuple[set[str], set[str]]:
    low = query.lower()
    roles, subs = set(), set()
    for cue, (role, sub) in _ROLE_CUES.items():
        if cue in low:
            roles.add(role)
            if sub:
                subs.add(sub)
    return roles, subs


@dataclass
class Result:
    text: str
    rerank_score: float
    meta: dict


@lru_cache(maxsize=1)
def _reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(model_name=RERANK_MODEL)


class Retriever:
    def __init__(self):
        self.tbl = open_table()
        self.rows = self.tbl.to_pandas().to_dict("records")
        self.bm25 = BM25Okapi([_tok(r["text"]) for r in self.rows])
        self.by_id = {r["id"]: r for r in self.rows}

    def search(self, query: str, k: int = 6, pool: int = 30, rerank_n: int = 15) -> list[Result]:
        # ① hybrid candidate generation
        qvec = embed_query(query)
        dense = self.tbl.search(qvec).limit(pool).to_pandas().to_dict("records")
        dense_rank = {r["id"]: i for i, r in enumerate(dense)}

        scores = self.bm25.get_scores(_tok(query))
        sparse_ids = [self.rows[i]["id"] for i in scores.argsort()[::-1][:pool]]
        sparse_rank = {cid: i for i, cid in enumerate(sparse_ids)}

        # RRF fusion
        k0 = 60
        rrf: dict[str, float] = {}
        for cid in set(dense_rank) | set(sparse_rank):
            rrf[cid] = 1 / (k0 + dense_rank.get(cid, pool)) + 1 / (k0 + sparse_rank.get(cid, pool))

        # ② metadata gate
        entity = detect_entity(query)
        roles, subs = role_hints(query)
        gated: list[tuple[str, float]] = []
        for cid, base in rrf.items():
            r = self.by_id[cid]
            if entity and not (r["entity"] == entity or r["entity_agnostic"]):
                continue  # HARD entity filter (with entity-agnostic escape hatch)
            if r["role"] == "reference":
                continue  # HARD exclude scaffolding
            s = base
            if r["role"] in roles:
                s += 0.01  # SOFT role boost
            if r["data_subtype"] and r["data_subtype"] in subs:
                s += 0.01
            if r["data_subtype"] == "conversion-table":
                s -= 0.02  # suppress the "7000-as-altitude" distractor
            gated.append((cid, s))

        gated.sort(key=lambda x: x[1], reverse=True)
        cand_ids = [cid for cid, _ in gated[:rerank_n]]
        if not cand_ids:
            return []

        # ③ cross-encoder rerank
        texts = [self.by_id[cid]["text"] for cid in cand_ids]
        rr = list(_reranker().rerank(query, texts))
        ranked = sorted(zip(cand_ids, rr), key=lambda x: x[1], reverse=True)[:k]
        return [Result(text=self.by_id[cid]["text"], rerank_score=float(s), meta=self.by_id[cid]) for cid, s in ranked]
