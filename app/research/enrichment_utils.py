"""Mesures et conversions de l'enrichissement et de la coherence (lot V3-5).

Fonctions pures, sans LLM : elles servent a l'instrumentation (Diagnostics) et
a la regeneration de section.
"""

import re
from typing import Any

# Citation brute produite par la synthese : [SOURCE:url], crochet fermant
# optionnel (generation coupee en limite de jetons).
_SOURCE_RE = re.compile(r"\[SOURCE:\s*(https?://[^\s\]]+)\]?")
# Citation numerotee produite par la conversion en bibliographie.
_NUMBERED_RE = re.compile(r"\[(\d+)\]")


def count_citations(text: str | None) -> int:
    """Nombre de citations ([SOURCE:url] ou [n]) presentes dans un texte."""
    if not text:
        return 0
    return len(_SOURCE_RE.findall(text)) + len(_NUMBERED_RE.findall(text))


def numbered_to_source(text: str, bibliography: list[dict[str, Any]]) -> str:
    """Remplace chaque [n] connu de la bibliographie par [SOURCE:url].

    Au moment de l'enrichissement les sections portent deja des [n] ; le
    modele doit voir les URL pour les conserver. Un [n] absent de la
    bibliographie est laisse tel quel.
    """
    by_id = {b.get("id"): b.get("url") for b in bibliography or [] if b.get("url")}

    def _sub(match: re.Match[str]) -> str:
        url = by_id.get(int(match.group(1)))
        return f"[SOURCE:{url}]" if url else match.group(0)

    return _NUMBERED_RE.sub(_sub, text or "")


def cited_urls(text: str | None) -> set[str]:
    """URL citees sous la forme [SOURCE:url] dans un texte."""
    return set(_SOURCE_RE.findall(text or ""))
