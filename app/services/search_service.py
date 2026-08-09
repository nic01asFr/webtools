"""
Service de recherche web via SearXNG.
"""

import logging
from typing import List, Optional
import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    """Résultat de recherche SearXNG"""
    url: str
    title: str
    content: str = ""
    engine: str = ""
    score: float = 0.0
    category: str = "general"


class SearXNGClient:
    """
    Client pour interroger SearXNG.
    Permet de rechercher des URLs pertinentes avant l'extraction.
    """

    def __init__(self, base_url: str = "http://searxng:8080"):
        """
        Initialise le client SearXNG.

        Args:
            base_url: URL de base de l'instance SearXNG
        """
        self.base_url = base_url
        logger.info(f"SearXNG client initialized with base_url: {base_url}")

    async def search(
        self,
        query: str,
        max_results: int = 10,
        language: str = "fr",
        categories: Optional[str] = None,
        engines: Optional[str] = None,
        time_range: Optional[str] = None,
        max_pages: int = 1
    ) -> List[SearchResult]:
        """
        Effectue une recherche via SearXNG.

        Args:
            query: Requête de recherche
            max_results: Nombre maximum de résultats
            language: Langue des résultats (fr, en, etc.)
            categories: Catégories de recherche (general, images, news, etc.)
            engines: Moteurs spécifiques (google, bing, duckduckgo, etc.)
            time_range: Filtre de fraîcheur ("day", "week", "month", "year")

        Returns:
            Liste de SearchResult
        """
        base_params = {
            "q": query,
            "format": "json",
            "language": language,
        }

        if categories:
            base_params["categories"] = categories

        if engines:
            base_params["engines"] = engines

        if time_range:
            base_params["time_range"] = time_range

        results: List[SearchResult] = []
        seen_urls = set()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Pagination : parcourt plusieurs pages SearXNG jusqu'a
                # atteindre max_results ou epuiser max_pages. Auparavant
                # toujours fige a pageno=1, ce qui plafonnait la couverture
                # a ~10-20 resultats quel que soit le besoin reel.
                for page in range(1, max_pages + 1):
                    params = {**base_params, "pageno": page}
                    logger.info(f"Searching SearXNG for: {query} (page {page})")

                    response = await client.get(f"{self.base_url}/search", params=params)
                    response.raise_for_status()
                    data = response.json()

                    page_results = data.get("results", [])
                    if not page_results:
                        break  # plus de resultats, inutile de continuer

                    for r in page_results:
                        url = r.get("url", "")
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        results.append(SearchResult(
                            url=url,
                            title=r.get("title", ""),
                            content=r.get("content", ""),
                            engine=r.get("engine", ""),
                            score=r.get("score", 0.0),
                            category=r.get("category", "general")
                        ))
                        if len(results) >= max_results:
                            break

                    if len(results) >= max_results:
                        break

                logger.info(f"Found {len(results)} results for query: {query}")
                return results

        except httpx.HTTPError as e:
            logger.error(f"HTTP error during SearXNG search: {e}")
            return results  # retourne ce qui a deja ete collecte, pas vide systematiquement
        except Exception as e:
            logger.error(f"Error during SearXNG search: {e}")
            return results

    async def health_check(self) -> bool:
        """
        Vérifie si SearXNG est accessible.

        Returns:
            True si SearXNG répond, False sinon
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/")
                return response.status_code == 200
        except Exception as e:
            logger.error(f"SearXNG health check failed: {e}")
            return False


# Instance globale du client
import os as _os
searxng_client = SearXNGClient(base_url=_os.getenv("SEARXNG_BASE_URL", "http://searxng:8080"))
