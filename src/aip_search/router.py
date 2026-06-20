"""Deterministic, LLM-free router (ARCHITECTURE.md §8).

Turns a query into the retrieval engine's input contract: an entity filter + target roles,
OR a clarify/abstain decision. The gazetteer is auto-derived from AD filenames, so entity
detection is a closed-set lookup (reliable) and we BLOCK on doubt rather than guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from .config import AIP_DIR

_ICAO_RE = re.compile(r"\bLI[A-Z]{2}\b")
_AIRSPACE_KW = re.compile(r"\b(tma|ctr|fir|uir|atz|fiz|spazio aereo|zona)\b", re.I)
_AIRPORT_TRIG = re.compile(r"\b(aeroport|aerodrom|scalo|torre|pista|piazzal|rullaggio)", re.I)
_AD_NAME_RE = re.compile(r"AD-2-(LI[A-Z]{2})---(.+?)-\d+\.pdf$")
_WORD_RE = re.compile(r"[a-zà-ÿ]{3,}")
# Capitalized or all-caps tokens — proper-noun candidates for foreign-airport detection.
_PLACE_RE = re.compile(r"([A-ZÀ-Ý][a-zà-ÿ]{2,}|[A-Z]{3,})")

_STOP = {"al", "di", "da", "del", "della", "san", "santa", "aeroporto", "airport", "riviera"}
_ACRONYMS = {
    "VFR", "IFR", "ATC", "ATS", "SSR", "TMA", "CTR", "FIR", "UIR", "ATZ", "ILS", "VOR",
    "NDB", "DME", "QNH", "QFE", "METAR", "NOTAM", "AIP", "ARP", "GPS", "RNAV", "RNP",
    "MHZ", "UTC", "AD", "ENR", "GEN", "VDS", "ACC", "APP", "TWR", "FIC", "FIS",
}

# Italian intent cues → roles / data sub-types to soft-boost.
_ROLE_CUES = {
    "frequenz": ("data", "frequency"), "freq": ("data", "frequency"),
    "transponder": ("rule", None), "ssr": ("rule", None), "squawk": ("rule", None),
    "codice": ("rule", None), "come": ("procedure", None), "procedura": ("procedure", None),
    "cosa significa": ("definition", None), "definizione": ("definition", None),
    "limiti": ("airspace", None), "spazio aereo": ("airspace", None),
    "vietat": ("warning", None), "proibit": ("warning", None), "pericolos": ("warning", None),
}


@dataclass
class Route:
    kind: str  # "search" | "clarify" | "abstain"
    entity_filter: set[str] | None = None
    roles: set[str] = field(default_factory=set)
    subs: set[str] = field(default_factory=set)
    candidates: list[tuple[str, str]] = field(default_factory=list)  # (icao, name)
    message: str | None = None


@lru_cache(maxsize=1)
def gazetteer() -> dict:
    """Auto-derive {icaos, name_token→icaos, icao→display name} from AD filenames."""
    icaos: set[str] = set()
    name_to_icaos: dict[str, set[str]] = {}
    icao_to_name: dict[str, str] = {}
    for p in AIP_DIR.glob("*_AD-2-*.pdf"):
        m = _AD_NAME_RE.search(p.name)
        if not m:
            continue
        icao, name = m.group(1), m.group(2)
        icaos.add(icao)
        icao_to_name.setdefault(icao, name.replace("-", " "))
        for tok in re.split(r"[-.]", name):
            tok = tok.lower()
            if len(tok) >= 3 and tok not in _STOP:
                name_to_icaos.setdefault(tok, set()).add(icao)
    return {"icaos": icaos, "name_to_icaos": name_to_icaos, "icao_to_name": icao_to_name}


def role_hints(query: str) -> tuple[set[str], set[str]]:
    low = query.lower()
    roles, subs = set(), set()
    for cue, (role, sub) in _ROLE_CUES.items():
        if cue in low:
            roles.add(role)
            if sub:
                subs.add(sub)
    return roles, subs


def _foreign_candidates(query: str) -> list[str]:
    """Capitalized/all-caps proper-noun tokens, excluding acronyms and the sentence start."""
    out = []
    for m in _PLACE_RE.finditer(query):
        tok = m.group(1)
        if m.start() == 0 or tok.upper() in _ACRONYMS:
            continue
        out.append(tok)
    return out


def route(query: str) -> Route:
    gaz = gazetteer()
    roles, subs = role_hints(query)

    # Airspace questions (TMA/CTR/FIR/…): content lives in entity-agnostic ENR chunks —
    # don't disambiguate the place as an airport; retrieve open with the airspace role.
    if _AIRSPACE_KW.search(query):
        roles.add("airspace")
        return Route("search", entity_filter=None, roles=roles, subs=subs)

    # Explicit ICAO code.
    raw_icaos = set(_ICAO_RE.findall(query.upper()))
    if raw_icaos:
        known = raw_icaos & gaz["icaos"]
        if known:
            return Route("search", entity_filter=known, roles=roles, subs=subs)
        return Route("abstain", message=f"Codice {', '.join(sorted(raw_icaos))} non presente nell'AIP italiano.")

    # Gazetteer name tokens. Intersect matched sets so "Roma Fiumicino" narrows to LIRF
    # while bare "Milano" stays ambiguous.
    matched = [gaz["name_to_icaos"][t] for t in _WORD_RE.findall(query.lower()) if t in gaz["name_to_icaos"]]
    if matched:
        inter = set.intersection(*matched)
        cand = inter if inter else set().union(*matched)
        if len(cand) == 1:
            return Route("search", entity_filter=cand, roles=roles, subs=subs)
        cands = sorted((ic, gaz["icao_to_name"][ic]) for ic in cand)
        return Route("clarify", candidates=cands, roles=roles, subs=subs,
                     message="Specifica quale aeroporto:")

    # Airport-shaped but no Italian match → likely a foreign/unknown airport → abstain.
    if _AIRPORT_TRIG.search(query):
        foreign = _foreign_candidates(query)
        if foreign:
            return Route("abstain",
                         message=f"Non risulta un aeroporto italiano (AIP) per: {', '.join(foreign)}.")

    # General question (no specific entity) → open retrieval.
    return Route("search", entity_filter=None, roles=roles, subs=subs)
