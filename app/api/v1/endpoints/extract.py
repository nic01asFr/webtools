"""
Endpoint d'extraction de contenu web.
"""

import logging
from fastapi import APIRouter, HTTPException, status

from app.api.models import ExtractRequest, ExtractResponse
from app.manager import ExtractorManager
from app.core.llm.factory import LLMFactory
from app.core.llm.base import LLMClientError

logger = logging.getLogger(__name__)

router = APIRouter()

# Instance globale du manager
manager = ExtractorManager()


@router.post(
    "/extract",
    response_model=ExtractResponse,
    status_code=status.HTTP_200_OK,
    summary="Extrait le contenu d'une URL",
    description="""
Extrait le contenu d'une page web avec détection automatique du type de contenu
et stratégies d'extraction intelligentes (direct Playwright ou agent IA).

Supporte plusieurs providers LLM : OpenAI, Anthropic (Claude), Albert.
"""
)
async def extract_web_content(request: ExtractRequest) -> ExtractResponse:
    """
    Extrait le contenu d'une URL.

    Args:
        request: Requête d'extraction avec URL et options

    Returns:
        Résultat de l'extraction avec le contenu

    Raises:
        HTTPException: En cas d'erreur de validation ou de configuration
    """
    try:
        # Créer le client LLM si configuré
        llm_client = None
        if request.llm_config:
            try:
                llm_client = LLMFactory.create(
                    provider=request.llm_config.provider,
                    api_key=request.llm_config.api_key,
                    model=request.llm_config.model,
                    base_url=request.llm_config.base_url
                )
                logger.info(f"Client LLM créé: {request.llm_config.provider}")
            except LLMClientError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Configuration LLM invalide: {str(e)}"
                )

        # Options d'extraction
        options = request.options or None

        # Effectuer l'extraction
        logger.info(f"Extraction demandée pour {request.url}")
        result = await manager.extract(
            url=request.url,
            prompt=request.prompt,
            extraction_type=request.extraction_type,
            llm_client=llm_client,
            options=options
        )

        # NE PAS fermer le client LLM ici : get_llm_client() renvoie une
        # instance PARTAGEE par tout le service. La fermer apres une
        # extraction rendait le client inutilisable pour tout le reste du
        # processus - "Cannot send a request, as the client has been closed",
        # remonte par le SDK comme un generique "Connection error".
        #
        # C'etait la cause des syntheses perdues dans research/deep : ce
        # pipeline declenche des dizaines d'extractions, la premiere fermait
        # le client partage et TOUTES les syntheses suivantes echouaient,
        # rendant un rapport vide avec success=true. Symptome trompeur : le
        # client teste isolement passait 15 appels enchaines sans un echec,
        # et research/quick (peu d'extractions) fonctionnait normalement.
        #
        # Le cycle de vie du client partage appartient a l'application, pas a
        # un endpoint.

        # Convertir en réponse API
        response = ExtractResponse.from_web_result(result)

        if not response.success:
            logger.warning(f"Extraction échouée pour {request.url}: {response.error}")

        return response

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"Erreur lors de l'extraction: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur interne lors de l'extraction: {str(e)}"
        )
