"""
Endpoint de recherche profonde (deep research).
"""

import logging
import time
from fastapi import APIRouter, HTTPException
from typing import List

from app.api.models import ResearchRequest, ResearchResponse
from app.services.search_service import searxng_client
from app.agents.research_agent import ResearchAgent
from app.core.llm import get_llm_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/research", response_model=ResearchResponse)
async def deep_research(request: ResearchRequest):
    """
    Effectue une recherche profonde avec navigation intelligente.

    **Fonctionnement:**
    1. Si aucune URL fournie → Recherche via SearXNG
    2. albert-code analyse chaque page
    3. Navigation vers les liens pertinents (jusqu'à max_depth)
    4. Synthèse finale de la réponse

    **Exemple de requête:**
    ```json
    {
      "query": "Comment configurer Traefik avec Docker ?",
      "start_urls": [],
      "max_depth": 2,
      "max_sources": 5
    }
    ```

    **Réponse:**
    - answer: Réponse synthétisée
    - sources: Liste des pages visitées avec extraits
    - navigation_path: Chemin de navigation suivi
    """
    start_time = time.time()

    try:
        logger.info(f"Research request: {request.query}")

        # 1. Obtenir les URLs de départ
        start_urls: List[str] = []

        if request.start_urls:
            # URLs fournies par l'utilisateur
            start_urls = [str(url) for url in request.start_urls]
            logger.info(f"Using {len(start_urls)} provided URLs")
        else:
            # Recherche via SearXNG
            logger.info("No URLs provided, searching via SearXNG...")

            search_results = await searxng_client.search(
                query=request.query,
                max_results=request.max_sources,
                language=request.language
            )

            if not search_results:
                return ResearchResponse.from_error(
                    query=request.query,
                    error_message="Aucun résultat trouvé via SearXNG"
                )

            start_urls = [r.url for r in search_results]
            logger.info(f"Found {len(start_urls)} URLs via SearXNG")

        # 2. Initialiser l'agent de recherche avec albert-code
        llm_client = await get_llm_client()

        research_agent = ResearchAgent(
            llm_client=llm_client,
            max_depth=request.max_depth,
            max_sources=request.max_sources
        )

        # 3. Effectuer la recherche profonde
        logger.info("Starting deep research with ResearchAgent...")

        results = await research_agent.research(
            query=request.query,
            start_urls=start_urls
        )

        # 4. Construire la réponse
        processing_time = time.time() - start_time

        response = ResearchResponse(
            success=True,
            query=request.query,
            answer=results["answer"],
            sources=results["sources"],
            navigation_path=results["navigation_path"],
            total_pages_visited=results["total_pages_visited"],
            total_links_analyzed=results["total_links_analyzed"],
            processing_time_seconds=round(processing_time, 2),
            metadata={
                "max_depth": request.max_depth,
                "max_sources": request.max_sources,
                "language": request.language,
                "llm_provider": llm_client.provider if hasattr(llm_client, "provider") else "unknown"
            }
        )

        logger.info(
            f"Research completed in {processing_time:.2f}s: "
            f"{len(results['sources'])} sources, {results['total_pages_visited']} pages visited"
        )

        return response

    except Exception as e:
        logger.error(f"Error during deep research: {e}", exc_info=True)

        processing_time = time.time() - start_time

        return ResearchResponse(
            success=False,
            query=request.query,
            error=f"Erreur lors de la recherche: {str(e)}",
            processing_time_seconds=round(processing_time, 2)
        )


@router.get("/research/health")
async def research_health():
    """
    Vérifie la santé du système de recherche.
    """
    health = {
        "research_agent": "ok",
        "searxng": "unknown",
        "llm": "unknown"
    }

    # Vérifier SearXNG
    try:
        is_healthy = await searxng_client.health_check()
        health["searxng"] = "ok" if is_healthy else "error"
    except Exception as e:
        health["searxng"] = f"error: {str(e)}"

    # Vérifier LLM
    try:
        llm_client = await get_llm_client()
        health["llm"] = "ok"
        health["llm_provider"] = llm_client.provider if hasattr(llm_client, "provider") else "unknown"
    except Exception as e:
        health["llm"] = f"error: {str(e)}"

    return health
