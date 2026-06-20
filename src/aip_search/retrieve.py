"""Retrieval engine: hybrid (dense ∪ BM25) → RRF → metadata gate → cross-encoder rerank.

Routing (entity filter, target roles) is decided upstream by router.route(); search() just
consumes the resulting filter and role hints.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from rank_bm25 import BM25Okapi

from .config import BACKEND, RERANK_MODEL
from .index import embed_query, open_table

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")


def _tok(s: str) -> list[str]:
    return _TOKEN_RE.findall(s.lower())


@dataclass
class Result:
    text: str
    rerank_score: float
    meta: dict


@lru_cache(maxsize=1)
def _reranker():
    if BACKEND == "torch":
        from sentence_transformers import CrossEncoder

        return ("ce", CrossEncoder(RERANK_MODEL))
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return ("fe", TextCrossEncoder(model_name=RERANK_MODEL))


class Retriever:
    def __init__(self):
        self.tbl = open_table()
        self.rows = self.tbl.to_pandas().to_dict("records")
        self.bm25 = BM25Okapi([_tok(r["text"]) for r in self.rows])
        self.by_id = {r["id"]: r for r in self.rows}

    def search(self, query: str, entity_filter: set[str] | None = None,
               roles: set[str] | None = None, subs: set[str] | None = None,
               k: int = 6, pool: int = 30, rerank_n: int = 15) -> list[Result]:
        roles, subs = roles or set(), subs or set()

        # ① hybrid candidate generation
        qvec = embed_query(query)
        dense = self.tbl.search(qvec).limit(pool).to_pandas().to_dict("records")
        dense_rank = {r["id"]: i for i, r in enumerate(dense)}
        scores = self.bm25.get_scores(_tok(query))
        sparse_ids = [self.rows[i]["id"] for i in scores.argsort()[::-1][:pool]]
        sparse_rank = {cid: i for i, cid in enumerate(sparse_ids)}

        k0 = 60
        rrf = {cid: 1 / (k0 + dense_rank.get(cid, pool)) + 1 / (k0 + sparse_rank.get(cid, pool))
               for cid in set(dense_rank) | set(sparse_rank)}

        # ② metadata gate
        gated: list[tuple[str, float]] = []
        for cid, base in rrf.items():
            r = self.by_id[cid]
            if entity_filter and not (r["entity"] in entity_filter or r["entity_agnostic"]):
                continue  # HARD entity filter (entity-agnostic escape hatch)
            if r["role"] == "reference":
                continue
            s = base
            if r["role"] in roles:
                s += 0.01
            if r["data_subtype"] and r["data_subtype"] in subs:
                s += 0.01
            if r["data_subtype"] == "conversion-table":
                s -= 0.02
            gated.append((cid, s))

        gated.sort(key=lambda x: x[1], reverse=True)
        cand_ids = [cid for cid, _ in gated[:rerank_n]]
        if not cand_ids:
            return []

        # ③ cross-encoder rerank
        texts = [self.by_id[cid]["text"] for cid in cand_ids]
        kind, model = _reranker()
        if kind == "ce":
            rr = [float(s) for s in model.predict([(query, t) for t in texts])]
        else:
            rr = list(model.rerank(query, texts))
        ranked = sorted(zip(cand_ids, rr), key=lambda x: x[1], reverse=True)[:k]
        return [Result(text=self.by_id[c]["text"], rerank_score=float(s), meta=self.by_id[c])
                for c, s in ranked]
