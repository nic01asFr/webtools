"""Filtre des resultats de recherche sans rapport avec la requete (C96).

Constat du 13/11/2026 sur la collecte enregistree du corpus citecheck :
- arXiv : 66 resultats, dont 43 partageaient moins de deux termes avec la
  requete ("Ad Orientem: the Orientation of Gothic Cathedrals of France"
  pour le financement du recul du trait de cote) : le moteur rend des
  articles qui contiennent un seul des mots ;
- bing : 17 resultats sur 22 hors sujet (magasins Lidl pour les
  neonicotinoides, fiches de composants Texas Instruments, pages zhihu),
  deja vu dans C84.
Ces resultats prenaient les places d'extraction de la section et
empechaient le repli de recherche de se declencher. Un resultat dont le
titre et l'extrait ne partagent aucun terme avec la requete est ecarte ;
pour les moteurs qui repondent par simple disjonction des mots, deux termes
sont exiges.

Les termes sont compares sur leurs six premiers caracteres, sans accents :
"microplastics" et "microplastiques", "hydrogen" et "hydrogene" se
rejoignent, ce qui garde les resultats d'une autre langue quand les mots
sont apparentes.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

STEM_CHARS = 6
MIN_WORD_CHARS = 4
# Moteurs qui rendent des documents contenant un seul des mots de la requete.
DISJUNCTIVE_ENGINES = frozenset({"arxiv"})

_OPERATOR = re.compile(r"\b(?:site|filetype|inurl|intitle):\S+", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    # francais
    "dans pour avec sans sont leur leurs plus moins entre depuis comme cette ceux celle "
    "elle elles nous vous quel quelle quels quelles sous vers chez dont tout tous toute "
    "toutes etre avoir fait faire mais donc ainsi alors aussi apres avant encore "
    # anglais
    "what when where which with from that this have been were their there about into "
    "over under than then these those such also more most other some only very will "
    "would could should".split()
)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def stems(text: str) -> set[str]:
    """Racines des mots significatifs d'un texte (operateurs de recherche exclus)."""
    words = _WORD.findall(_fold(_OPERATOR.sub(" ", text or "")))
    return {w[:STEM_CHARS] for w in words if len(w) >= MIN_WORD_CHARS and w not in _STOPWORDS}


def is_relevant(query_stems: set[str], title: str, snippet: str, engine: str) -> bool:
    """Vrai si le resultat partage assez de termes avec la requete."""
    if not query_stems:
        return True
    needed = 2 if (engine or "").strip().lower() in DISJUNCTIVE_ENGINES else 1
    overlap = len(query_stems & stems(f"{title} {snippet}"))
    return overlap >= min(needed, len(query_stems))


def keep_relevant(query: str, results: Iterable[Any]) -> tuple[list[Any], int]:
    """Resultats pertinents (objets avec title, content, engine) et nombre ecartes."""
    query_stems = stems(query)
    kept: list[Any] = []
    dropped = 0
    for r in results or []:
        if is_relevant(
            query_stems,
            getattr(r, "title", "") or "",
            getattr(r, "content", "") or "",
            getattr(r, "engine", "") or "",
        ):
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped
