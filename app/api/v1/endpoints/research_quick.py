"""
Endpoint de recherche rapide documentee (/research/quick).

Version legere de /research/deep : une recherche, quelques extractions,
une seule synthese avec citations - pas l'orchestration complete en 4 phases.
"""

import logging
import re
import unicodedata

from fastapi import APIRouter, HTTPException, status

from app.api.models import (
    ExtractionOptions,
    QuickResearchDiscardedSource,
    QuickResearchRequest,
    QuickResearchResponse,
    QuickResearchSource,
)
from app.core.llm import get_llm_client
from app.core.llm.base import LLMClientError, LLMEmptyContentError, LLMTimeoutError
from app.manager import ExtractorManager
from app.services.search_service import searxng_client

logger = logging.getLogger(__name__)
router = APIRouter()

manager = ExtractorManager()

# Caracteres gardes par source quand la selection de passages est inactive.
SOURCE_HEAD_CHARS = 2000
_WORD_RE = re.compile(r"\w{3,}", re.UNICODE)

# Messages renvoyes au client : un par categorie, le detail va dans les journaux.
MSG_SEARCH_DOWN = "Moteur de recherche indisponible"
MSG_LLM_EMPTY = "Le modele de langage a rendu une reponse vide"
MSG_LLM_TIMEOUT = "Le modele de langage n'a pas repondu a temps"
MSG_LLM_ERROR = "Erreur du modele de langage"
MSG_INTERNAL = "Erreur interne lors de la recherche rapide"


def _tokens(text: str) -> set[str]:
    """Mots de 3 lettres ou plus, en minuscules et sans accents."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return set(_WORD_RE.findall(folded))


def select_passages(query: str, documents: list[dict], budget_chars: int) -> list[str]:
    """
    Rend, pour chaque document, le texte forme de ses passages les plus
    pertinents pour la question (recouvrement lexical), dans l'ordre du
    document, dans la limite d'un budget total de caracteres.

    documents : liste de dicts {title, url, content}. Le resultat suit le meme
    ordre ; un document sans passage retenu rend une chaine vide.
    """
    from app.utils.chunking import document_to_chunk_items

    query_tokens = _tokens(query)
    candidates: list[tuple[float, int, int, str]] = []
    for doc_index, doc in enumerate(documents):
        items = document_to_chunk_items(doc["url"], doc["title"], doc["content"])
        for item in items:
            overlap = len(query_tokens & _tokens(item["content"]))
            score = overlap / len(query_tokens) if query_tokens else 0.0
            candidates.append((score, doc_index, item["metadata"]["chunk_index"], item["content"]))

    # Meilleur score d'abord ; a score egal, ordre du document puis du passage.
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    kept: dict[int, list[tuple[int, str]]] = {}
    used = 0
    for _score, doc_index, chunk_index, text in candidates:
        if used + len(text) > budget_chars and used > 0:
            continue
        kept.setdefault(doc_index, []).append((chunk_index, text))
        used += len(text)

    return [
        "\n[...]\n".join(text for _i, text in sorted(kept.get(i, [])))
        for i in range(len(documents))
    ]


def _quick_uses_chunk_selection() -> bool:
    from app.research.pipeline_config import PipelineConfig

    return PipelineConfig.from_env().quick_uses_chunk_selection


@router.post("/research/quick", response_model=QuickResearchResponse, summary="Reponse rapide documentee")
async def research_quick(request: QuickResearchRequest) -> QuickResearchResponse:
    """
    Recherche une question factuelle : trouve des sources via SearXNG,
    extrait leur contenu (chaine RSS -> direct -> LLM leger -> agent, deja
    durcie), puis synthetise une reponse courte avec citations.

    La reponse dit combien de sources ont ete tentees, extraites et ecartees.
    Un modele qui ne repond rien ou une panne du moteur donnent une erreur
    HTTP (502 ou 504), pas une reponse vide en succes.
    """
    try:
        try:
            search_results = await searxng_client.search(
                query=request.query, max_results=request.max_sources
            )
        except Exception:
            logger.exception("Recherche rapide : echec SearXNG pour '%s'", request.query)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=MSG_SEARCH_DOWN)

        if not search_results:
            return QuickResearchResponse.from_error(
                query=request.query, error_message="Aucune source trouvee"
            )

        llm_client = await get_llm_client()

        attempted = search_results[: request.max_sources]
        extracted: list[dict] = []
        discarded: list[QuickResearchDiscardedSource] = []
        for r in attempted:
            try:
                result = await manager.extract(
                    url=r.url, prompt=request.query, extraction_type="general",
                    llm_client=llm_client, options=ExtractionOptions(timeout=30)
                )
            except Exception as e:
                logger.warning("Extraction echouee pour %s: %s", r.url, e)
                discarded.append(QuickResearchDiscardedSource(url=r.url, reason="extraction_echouee"))
                continue
            if not result.success:
                logger.warning("Extraction sans succes pour %s: %s", r.url, result.error)
                discarded.append(QuickResearchDiscardedSource(url=r.url, reason="extraction_echouee"))
            elif not result.content:
                discarded.append(QuickResearchDiscardedSource(url=r.url, reason="contenu_vide"))
            else:
                extracted.append({
                    "title": r.title, "url": r.url, "content": result.content,
                    "score": getattr(r, "score", 0.0),
                })

        if not extracted:
            return QuickResearchResponse.from_error(
                query=request.query, error_message="Aucune source exploitable",
                sources_attempted=len(attempted), discarded=discarded,
            )

        if _quick_uses_chunk_selection():
            texts = select_passages(
                request.query, extracted, budget_chars=SOURCE_HEAD_CHARS * len(extracted)
            )
        else:
            texts = [e["content"][:SOURCE_HEAD_CHARS] for e in extracted]

        context = "\n\n".join(
            f"[Source {i+1}: {e['title']}]\n{text}" for i, (e, text) in enumerate(zip(extracted, texts))
        )
        try:
            answer = await llm_client.generate(messages=[
                {"role": "system", "content": (
                    "Tu reponds a une question factuelle en te basant UNIQUEMENT sur les "
                    "sources fournies. Cite tes sources avec [1], [2] etc. Sois concis "
                    "(quelques phrases). Si les sources ne permettent pas de repondre, dis-le."
                )},
                {"role": "user", "content": f"Question: {request.query}\n\nSources:\n{context}"}
            ])
        except LLMEmptyContentError:
            logger.error("Recherche rapide : reponse vide du modele pour '%s'", request.query)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=MSG_LLM_EMPTY)
        except LLMTimeoutError:
            logger.error("Recherche rapide : echeance du modele pour '%s'", request.query)
            raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail=MSG_LLM_TIMEOUT)
        except LLMClientError:
            logger.exception("Recherche rapide : erreur du modele pour '%s'", request.query)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=MSG_LLM_ERROR)

        if not answer or not answer.strip():
            logger.error("Recherche rapide : reponse vide du modele pour '%s'", request.query)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=MSG_LLM_EMPTY)

        # La pertinence est le score du moteur de recherche, quand il existe.
        sources = [
            QuickResearchSource(
                title=e["title"], url=e["url"], relevance=e["score"] if e["score"] > 0 else None,
                content=text if request.include_source_content else None,
            )
            for e, text in zip(extracted, texts)
        ]
        return QuickResearchResponse(
            query=request.query, answer=answer, sources=sources,
            sources_attempted=len(attempted), sources_extracted=len(extracted),
            sources_discarded=len(discarded), discarded=discarded, success=True,
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Erreur research_quick pour '%s'", request.query)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=MSG_INTERNAL)
