"""
Endpoint d'extraction de contenu web.
"""

import logging
from fastapi import APIRouter, HTTPException, status

from app.api.models import ExtractRequest, ExtractResponse
from app.manager import ExtractorManager
from app.core.llm import get_llm_client
from app.core.llm.base import BaseLLMClient, LLMClientError
from app.core.llm.factory import LLMFactory
from app.api.models import LLMConfig
from app.utils.url_safety import UnsafeURLError, assert_safe_url_async

logger = logging.getLogger(__name__)

router = APIRouter()

# Instance globale du manager
manager = ExtractorManager()

# Messages renvoyes au client : un par categorie, le detail va dans les journaux.
MSG_BAD_LLM_CONFIG = "Configuration LLM invalide"
MSG_UNSAFE_BASE_URL = "llm_config.base_url refusee (adresse non publique ou invalide)"
MSG_INTERNAL = "Erreur interne lors de l'extraction"


async def _create_caller_client(llm_config: LLMConfig) -> BaseLLMClient:
    """
    Cree le client LLM de la configuration fournie par l'appelant.

    base_url passe d'abord par la garde SSRF : sinon le serveur enverrait le
    texte de la page a un hote arbitraire. Leve HTTPException 400 si elle est
    refusee ou si la configuration est invalide.
    """
    if llm_config.base_url:
        try:
            await assert_safe_url_async(llm_config.base_url)
        except UnsafeURLError as e:
            logger.warning("llm_config.base_url refusee (SSRF): %s", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=MSG_UNSAFE_BASE_URL
            )
    try:
        client = LLMFactory.create(
            provider=llm_config.provider,
            api_key=llm_config.api_key,
            model=llm_config.model,
            base_url=llm_config.base_url
        )
    except LLMClientError as e:
        logger.warning("Configuration LLM invalide: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=MSG_BAD_LLM_CONFIG
        )
    logger.info("Client LLM créé: %s", llm_config.provider)
    return client


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
    # Client propre a la requete, a fermer en fin de traitement. Reste None
    # quand l'appelant utilise la configuration du service (client partage).
    own_client = None
    try:
        if request.llm_config:
            llm_client = own_client = await _create_caller_client(request.llm_config)
        else:
            llm_client = await get_llm_client()

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
            detail=MSG_INTERNAL
        )

    finally:
        # Seul un client cree pour cette requete est ferme : le client partage
        # de get_llm_client() appartient a l'application.
        if own_client is not None:
            await own_client.close()
