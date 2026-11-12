"""
Endpoint pour l'analyse d'images avec Albert Vision (albert-large).
"""

import logging
import time
from fastapi import APIRouter, HTTPException, status

from app.api.models import VisionRequest, VisionResponse
from app.core.config import settings
from app.core.llm import get_llm_client
from app.core.llm.base import LLMClientError, LLMEmptyContentError, LLMTimeoutError
from app.utils.url_safety import assert_safe_url_async, UnsafeURLError

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
    Analyse une image avec Albert Vision (albert-large).

    **Paramètres:**
    - **image_url**: URL publique de l'image (PNG, JPG, WebP, GIF)
    - **prompt**: Question ou instruction (5-1000 caractères)
    - **system_prompt**: Contexte optionnel pour guider l'analyse
    - **temperature**: Créativité 0.0-2.0 (défaut: 0.15, recommandé pour analyse factuelle)
    - **max_tokens**: Longueur réponse 50-4096 (défaut: 500)

    **Exemples de requête:**
    ```json
    {
      "image_url": "https://example.com/chart.png",
      "prompt": "Quelles tendances vois-tu dans ce graphique ?",
      "system_prompt": "Tu es un expert en analyse de données",
      "temperature": 0.15,
      "max_tokens": 500
    }
    ```

    **Exemple de réponse:**
    ```json
    {
      "success": true,
      "image_url": "https://example.com/chart.png",
      "prompt": "Quelles tendances...",
      "analysis": "Ce graphique montre une croissance de 25%...",
      "model_used": "albert-large",
      "processing_time_seconds": 8.2
    }
    ```

    **Cas d'usage:**
    - 📊 Analyser des graphiques et tableaux
    - 📝 OCR - Extraire du texte depuis images
    - 🗺️ Analyser des cartes géographiques
    - 🎨 Décrire des logos, UI, photos
    - 📋 Extraire des données de documents visuels

    **Performances:**
    - OCR simple: ~1-2s
    - Description: ~4-6s
    - Analyse complexe: ~8-12s
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

    try:
        logger.info("Analyzing image: %s", image_url)

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
            image_url=image_url,
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
