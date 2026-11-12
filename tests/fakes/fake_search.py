"""Fausse recherche : resultats par requete, requetes enregistrees."""

import re
from dataclasses import dataclass
from re import Pattern
from typing import Any

from app.services.search_service import SearchResult


def make_result(
    url: str, title: str = "", content: str = "", engine: str = "fake", score: float = 1.0
) -> SearchResult:
    """SearchResult du service reel (url, title, content, engine, score, category)."""
    return SearchResult(url=url, title=title or url, content=content, engine=engine, score=score)


@dataclass
class SearchCall:
    """Une requete recue, avec tous ses parametres."""

    query: str
    max_results: int
    language: str
    categories: str | None
    engines: str | None
    time_range: str | None
    max_pages: int


class FakeSearch:
    """Double de SearXNGClient (methodes search et health_check), sans reseau.

    Scenarisation : add(requete, resultats). `requete` est une chaine
    (egalite exacte) ou un motif compile (re.search) ; les egalites exactes
    passent d'abord, puis les motifs dans l'ordre d'enregistrement. Sans
    correspondance : `default` (liste vide par defaut, comme un moteur qui ne
    trouve rien). `resultats` : liste de SearchResult (voir make_result), ou
    une exception (levee).

    Enregistrement : `queries` (liste de SearchCall, dans l'ordre),
    queries_text() pour les seules chaines.
    """

    def __init__(
        self,
        results: dict[str, list[SearchResult]] | None = None,
        default: list[SearchResult] | None = None,
    ) -> None:
        self.queries: list[SearchCall] = []
        self._exact: dict[str, Any] = dict(results or {})
        self._patterns: list[tuple[Pattern[str], Any]] = []
        self.default: list[SearchResult] = list(default or [])

    def add(self, query: str | Pattern[str], results: Any) -> "FakeSearch":
        if isinstance(query, re.Pattern):
            self._patterns.append((query, results))
        else:
            self._exact[query] = results
        return self

    def queries_text(self) -> list[str]:
        return [c.query for c in self.queries]

    async def search(
        self,
        query: str,
        max_results: int = 10,
        language: str = "all",
        categories: str | None = None,
        engines: str | None = None,
        time_range: str | None = None,
        max_pages: int = 1,
    ) -> list[SearchResult]:
        self.queries.append(
            SearchCall(query, max_results, language, categories, engines, time_range, max_pages)
        )
        found: Any = self.default
        if query in self._exact:
            found = self._exact[query]
        else:
            for regex, results in self._patterns:
                if regex.search(query):
                    found = results
                    break
        if isinstance(found, BaseException):
            raise found
        return list(found)[:max_results]

    async def health_check(self) -> bool:
        return True
