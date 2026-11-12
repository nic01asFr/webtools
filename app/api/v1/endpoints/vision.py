"""
Endpoint d'analyse d'images par le modele de vision configure (settings).
"""

import base64
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, status

from app.api.models import VisionRequest, VisionResponse
from app.core.config import settings
from app.core.llm import get_llm_client
from app.core.llm.base import LLMClientError, LLMEmptyContentError, LLMTimeoutError
from app.utils.url_safety import UnsafeURLError, assert_safe_url_async, safe_request_hooks

logger = logging.getLogger(__name__)

router = APIRouter()


# Messages renvoyes au client : un par categorie, le detail va dans les journaux.
MSG_NO_KEY = "Cle API du modele de langage non configuree"
MSG_UNSAFE_URL = "URL d'image refusee (adresse non publique ou invalide)"
MSG_NO_VISION = "Le modele configure ne gere pas l'analyse d'images"
MSG_LLM_EMPTY = "Le modele a rendu une analyse vide"
MSG_LLM_TIMEOUT = "Le modele n'a pas repondu a temps"
MSG_LLM_ERROR = "Erreur du modele de langage"
MSG_INTERNAL = "Erreur interne lors de l'analyse d'image"


MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_FETCH_TIMEOUT = 15.0
# Identifiant de navigateur : des hebergeurs (Wikimedia entre autres) refusent
# en 403 les clients sans User-Agent reconnu.
IMAGE_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 WebtoolsBot/1.0"
)
_IMAGE_TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests


async def fetch_image_as_data_url(url: str) -> str | None:
    """
    Telecharge l'image et la rend en data URL base64, ou None en cas d'echec.

    Sans ce telechargement, c'est le proxy LLM qui va chercher l'image : observe
    en production, upload.wikimedia.org lui repondait 403 et l'analyse
    echouait en 502 alors que l'image est publique. Telechargee ici, sous la
    garde SSRF (chaque redirection revalidee), avec une taille plafonnee.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=IMAGE_FETCH_TIMEOUT,
            headers={"User-Agent": IMAGE_USER_AGENT, "Accept": "image/*"},
            event_hooks=safe_request_hooks(),
            transport=_IMAGE_TRANSPORT,
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    logger.warning("Image %s : HTTP %s", url, response.status_code)
                    return None
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if not content_type.startswith("image/"):
                    logger.warning("Image %s : type %r refuse", url, content_type)
                    return None
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        logger.warning("Image %s : plus de %d octets, refusee", url, MAX_IMAGE_BYTES)
                        return None
                    chunks.append(chunk)
    except UnsafeURLError:
        raise
    except Exception as exc:  # noqa: BLE001 - repli sur l'URL, cause journalisee
        logger.warning("Telechargement de l'image %s impossible : %s", url, exc)
        return None
    return f"data:{content_type};base64,{base64.b64encode(b''.join(chunks)).decode('ascii')}"


@router.get("/vision/health")
async def vision_health():
    """Verifie que la configuration LLM permet l'analyse d'images (sans appel au modele)."""
    if not settings.default_llm_api_key:
        return {
            "vision": "unavailable",
            "reason": "API key not configured"
        }

    return {
        "vision": "ok",
        "model": settings.default_llm_model,
        "base_url": settings.default_llm_base_url
    }


@router.post("/vision", response_model=VisionResponse)
async def analyze_image(request: VisionRequest):
    """
    Analyse une image avec le modele LLM configure pour le service.

    Le modele doit declarer la capacite vision (supports_vision), sinon 501.
    L'URL de l'image passe par la garde SSRF (400 si elle vise une adresse
    interne). Sans cle LLM configuree : 503 ; analyse vide : 502 ; delai
    depasse : 504. Aucun temps de reponse n'est garanti : il depend du modele.

    Parametres : image_url, prompt, system_prompt (optionnel), temperature,
    max_tokens (voir VisionRequest).
    """
    start_time = time.time()
    image_url = str(request.image_url)

    # Garde SSRF : image_url part vers un fournisseur LLM qui va la
    # recuperer, mais selon la configuration c'est parfois ce service qui
    # fetch. Ce chemin contournait ExtractorManager (ou la garde est
    # normalement posee), donc n'etait pas couvert.
    try:
        await assert_safe_url_async(image_url)
    except UnsafeURLError as e:
        logger.warning("Analyse refusee (SSRF) pour %s: %s", image_url, e)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=MSG_UNSAFE_URL)

    # Client partage, configure comme le reste du service ; il appartient a
    # l'application et n'est pas ferme ici.
    try:
        llm_client = await get_llm_client()
    except ValueError:
        # Aucune cle configuree : get_llm_client() leve avant de rendre un client.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=MSG_NO_KEY)
    if not llm_client.api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=MSG_NO_KEY)
    if not llm_client.supports_vision:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail=MSG_NO_VISION)

    # Image telechargee par ce service ; en cas d'echec, l'URL est transmise
    # telle quelle et le fournisseur tente lui-meme (comportement anterieur).
    try:
        image_payload = await fetch_image_as_data_url(image_url) or image_url
    except UnsafeURLError as e:
        logger.warning("Redirection de l'image refusee (SSRF) pour %s: %s", image_url, e)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=MSG_UNSAFE_URL)

    try:
        logger.info("Analyzing image: %s (%s)", image_url, "telechargee" if image_payload != image_url else "URL directe")

        # Timeout court specifique a la vision : une analyse d'image reussie
        # prend <10s dans tous les cas observes. Le timeout global de 90s
        # (necessaire pour les syntheses longues de research_deep) laisserait
        # une requete sur image inaccessible tourner bien trop longtemps.
        # max_retries=0 ici : le retry global (5 tentatives) sert a absorber
        # les erreurs transitoires des syntheses longues de research_deep,
        # mais une image inaccessible (mauvais domaine, 404...) est un echec
        # permanent - le retenter 5 fois ne fait que multiplier le temps
        # d'attente pour un resultat qui ne changera jamais.
        analysis = await llm_client.generate_with_vision(
            text=request.prompt,
            image_url=image_payload,
            system_prompt=request.system_prompt,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            timeout=20.0,
            max_retries=0
        )
        if not analysis or not analysis.strip():
            raise LLMEmptyContentError("analyse vide")

        processing_time = time.time() - start_time
        logger.info("Vision analysis completed in %.2fs", processing_time)

        return VisionResponse(
            success=True,
            image_url=image_url,
            prompt=request.prompt,
            analysis=analysis,
            model_used=llm_client.model,
            processing_time_seconds=round(processing_time, 2)
        )

    except LLMEmptyContentError:
        logger.error("Analyse vide du modele pour %s", image_url)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=MSG_LLM_EMPTY)
    except LLMTimeoutError:
        logger.error("Echeance du modele pour %s", image_url)
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail=MSG_LLM_TIMEOUT)
    except LLMClientError:
        logger.exception("Erreur du modele pour %s", image_url)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=MSG_LLM_ERROR)
    except Exception:
        logger.exception("Error analyzing image %s", image_url)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=MSG_INTERNAL)
