"""
Endpoint de recherche rapide documentee (/research/quick).

Version legere de /research/deep : une recherche, quelques extractions,
une seule synthese avec citations - pas l'orchestration complete en 4 phases.
"""

import logging
import os
import re
from fastapi import APIRouter

from app.api.models import QuickResearchRequest, QuickResearchResponse, QuickResearchSource
from app.services.search_service import searxng_client
from app.manager import ExtractorManager
from app.core.llm import get_llm_client
from app.api.models import ExtractionOptions

logger = logging.getLogger(__name__)
router = APIRouter()

manager = ExtractorManager()


@router.post("/research/quick", response_model=QuickResearchResponse, summary="Reponse rapide documentee")
async def research_quick(request: QuickResearchRequest) -> QuickResearchResponse:
    """
    Recherche une question factuelle : trouve des sources via SearXNG,
    extrait leur contenu (chaine RSS -> direct -> LLM leger -> agent, deja
    durcie), puis synthetise une reponse courte avec citations.
    """
    try:
        search_results = await searxng_client.search(
            query=request.query, max_results=request.max_sources
        )
        if not search_results:
            return QuickResearchResponse.from_error(
                query=request.query, error_message="Aucune source trouvee"
            )

        llm_client = await get_llm_client()

        extracted = []
        for r in search_results[:request.max_sources]:
            try:
                result = await manager.extract(
                    url=r.url, prompt=request.query, extraction_type="general",
                    llm_client=llm_client, options=ExtractionOptions(timeout=30)
                )
                if result.success and result.content:
                    extracted.append({"title": r.title, "url": r.url, "content": result.content[:2000]})
            except Exception as e:
                logger.warning(f"Extraction echouee pour {r.url}: {e}")
                continue

        if not extracted:
            return QuickResearchResponse.from_error(
                query=request.query, error_message="Aucune source exploitable"
            )

        context = "\n\n".join(
            f"[Source {i+1}: {e['title']}]\n{e['content']}" for i, e in enumerate(extracted)
        )
        answer_raw = await llm_client.generate(messages=[
            {"role": "system", "content": (
                "Tu reponds a une question factuelle en te basant UNIQUEMENT sur les "
                "sources fournies. Cite tes sources avec [1], [2] etc. Sois concis "
                "(quelques phrases). Si les sources ne permettent pas de repondre, dis-le."
            )},
            {"role": "user", "content": f"Question: {request.query}\n\nSources:\n{context}"}
        ])

        sources = [
            QuickResearchSource(title=e["title"], url=e["url"], relevance=1.0 - (i * 0.1))
            for i, e in enumerate(extracted)
        ]
        confidence = "high" if len(extracted) >= 3 else ("medium" if len(extracted) >= 1 else "low")

        return QuickResearchResponse(
            query=request.query, answer=answer_raw, sources=sources,
            confidence=confidence, success=True
        )

    except Exception as e:
        logger.error(f"Erreur research_quick pour '{request.query}': {e}")
        return QuickResearchResponse.from_error(query=request.query, error_message=str(e))
