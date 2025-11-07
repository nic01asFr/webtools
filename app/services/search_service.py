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
        engines: Optional[str] = None
    ) -> List[SearchResult]:
        """
        Effectue une recherche via SearXNG.

        Args:
            query: Requête de recherche
            max_results: Nombre maximum de résultats
            language: Langue des résultats (fr, en, etc.)
            categories: Catégories de recherche (general, images, news, etc.)
            engines: Moteurs spécifiques (google, bing, duckduckgo, etc.)

        Returns:
            Liste de SearchResult
        """
        params = {
            "q": query,
            "format": "json",
            "language": language,
            "pageno": 1
        }

        if categories:
            params["categories"] = categories

        if engines:
            params["engines"] = engines

        try:
            logger.info(f"Searching SearXNG for: {query}")

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/search",
                    params=params
                )
                response.raise_for_status()

                data = response.json()

                # Extraire les résultats
                results = []
                for r in data.get("results", [])[:max_results]:
                    results.append(SearchResult(
                        url=r.get("url", ""),
                        title=r.get("title", ""),
                        content=r.get("content", ""),
                        engine=r.get("engine", ""),
                        score=r.get("score", 0.0),
                        category=r.get("category", "general")
                    ))

                logger.info(f"Found {len(results)} results for query: {query}")
                return results

        except httpx.HTTPError as e:
            logger.error(f"HTTP error during SearXNG search: {e}")
            return []
        except Exception as e:
            logger.error(f"Error during SearXNG search: {e}")
            return []

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
searxng_client = SearXNGClient()
