"""
Endpoint de recherche web simple (via SearXNG).
"""

import logging
from fastapi import APIRouter

from app.api.models import SearchRequest, SearchResponse, SimpleSearchResult
from app.services.search_service import searxng_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/search", response_model=SearchResponse, summary="Recherche web simple")
async def search_web(request: SearchRequest) -> SearchResponse:
    """
    Recherche web simple via SearXNG. Retourne titre, URL et extrait pour
    chaque resultat. Ne fait aucune extraction de contenu ni appel LLM -
    le palier le plus economique de l'API.
    """
    try:
        results = await searxng_client.search(
            query=request.query,
            max_results=request.max_results,
            language=request.language,
            categories=request.categories,
            engines=request.engines
        )
        simple_results = [
            SimpleSearchResult(title=r.title, url=r.url, snippet=r.content[:300])
            for r in results
        ]
        return SearchResponse(
            query=request.query,
            results=simple_results,
            total=len(simple_results),
            success=True
        )
    except Exception as e:
        logger.error(f"Erreur recherche pour '{request.query}': {e}")
        return SearchResponse.from_error(query=request.query, error_message=str(e))
