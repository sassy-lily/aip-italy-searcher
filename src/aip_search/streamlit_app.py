"""Streamlit UI for AIP-Search.

Run:  uv run streamlit run src/aip_search/streamlit_app.py

No persistent service: @st.cache_resource keeps the Retriever (and, after the first query,
the embedder/reranker) resident for the server's lifetime, so only the first query bears the
cold-start. The LLM runs on Ollama (iGPU).
"""
from __future__ import annotations

import streamlit as st

from aip_search.answer import answer
from aip_search.retrieve import Retriever

st.set_page_config(page_title="AIP-Search", page_icon="🛩️", layout="centered")

_TIER_BADGE = {
    "confident": ("✅ Affidabile", "success"),
    "hedged": ("⚠️ Non sono certo — verifica nelle fonti", "warning"),
    "abstain": ("⛔ Nessuna risposta nelle fonti", "error"),
}


@st.cache_resource(show_spinner="Carico l'indice e i modelli (solo al primo avvio)…")
def get_retriever() -> Retriever:
    return Retriever()


def _render(res, question: str) -> None:
    if res.kind == "abstain":
        st.warning(res.message)
        return

    if res.kind == "clarify":
        st.info(res.message)
        cols = st.columns(len(res.candidates))
        for col, (icao, name) in zip(cols, res.candidates):
            if col.button(f"{icao} · {name}", key=f"cand_{icao}", use_container_width=True):
                st.session_state.pending_q = question
                st.session_state.force_entity = {icao}
                st.rerun()
        return

    # kind == "answer"
    st.caption(f"Dati AIP · ciclo AIRAC {res.cycle or 'n/d'} · strumento di consultazione, non per uso operativo")
    label, kind = _TIER_BADGE[res.tier]
    getattr(st, kind)(label)

    if res.tier == "abstain":
        if res.sources:
            st.markdown("**Fonti più vicine:**")
            _render_sources(res.sources)
        return

    answer_md = "\n".join(
        f"- {text} " + "".join(f"<sup>**[{n}]**</sup>" for n in marks)
        for text, marks in res.claims
    )
    st.markdown(answer_md, unsafe_allow_html=True)
    for desc, ptr, reason in res.gaps:
        st.markdown(f"> *Lacuna:* {desc}" + (f" → vedi **{ptr}**" if ptr else ""))

    st.subheader("Fonti")
    _render_sources(res.sources)

    if res.flags:
        with st.expander("Note di verifica (guardrail)"):
            for f in res.flags:
                st.text(f"· {f}")


def _render_sources(sources) -> None:
    for n, meta, text in sources:
        role = meta["role"] + (f"/{meta['data_subtype']}" if meta["data_subtype"] else "")
        ent = f" · {meta['entity']}" if meta["entity"] else ""
        with st.container(border=True):
            st.markdown(
                f"**[{n}] {meta['section_code']}** · {role}{ent} · "
                f"AIRAC {meta['airac_effective_date'] or 'n/d'}"
            )
            st.caption(text[:320].replace("\n", " "))
            if meta["source_url"]:
                st.markdown(f"[Apri la fonte ufficiale ↗]({meta['source_url']})")


def main() -> None:
    st.title("🛩️ AIP-Search")
    st.caption(
        "Consultazione locale dell'AIP italiano + normativa VDS. "
        "**Strumento di consultazione, non per uso operativo** — verifica sempre la fonte ufficiale citata."
    )

    ss = st.session_state
    ss.setdefault("force_entity", None)
    ss.setdefault("pending_q", None)

    question = st.text_input(
        "Domanda (in italiano)",
        placeholder="Es. Che codice transponder si usa in VFR non in contatto con l'ATC?",
    )
    run = st.button("Cerca", type="primary")

    # A clarify candidate was clicked on the previous run.
    if ss.force_entity and ss.pending_q:
        q = ss.pending_q
        force = ss.force_entity
        ss.force_entity = None
        ss.pending_q = None
        st.markdown(f"**Domanda:** {q}")
        with st.spinner("Cerco e sintetizzo…"):
            res = answer(q, get_retriever(), force_entity=force)
        _render(res, q)
    elif run and question.strip():
        with st.spinner("Cerco e sintetizzo…"):
            res = answer(question, get_retriever())
        _render(res, question)


main()
