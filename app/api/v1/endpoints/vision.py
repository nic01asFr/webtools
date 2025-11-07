"""
Endpoint pour l'analyse d'images avec Albert Vision (albert-large).
"""

import logging
import time
from fastapi import APIRouter, HTTPException

from app.api.models import VisionRequest, VisionResponse
from app.core.llm.factory import LLMFactory

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/vision/health")
async def vision_health():
    """Vérifie la disponibilité de l'analyse vision."""
    try:
        # Vérifier que albert-large est disponible
        import os
        api_key = os.getenv("DEFAULT_LLM_API_KEY", "")
        base_url = os.getenv("DEFAULT_LLM_BASE_URL", "https://albert.api.etalab.gouv.fr")

        if not api_key:
            return {
                "vision": "unavailable",
                "reason": "API key not configured"
            }

        return {
            "vision": "ok",
            "model": "albert-large",
            "base_url": base_url
        }
    except Exception as e:
        logger.error(f"Vision health check failed: {e}")
        return {
            "vision": "error",
            "error": str(e)
        }


@router.post("/vision", response_model=VisionResponse)
async def analyze_image(request: VisionRequest):
    """
    Analyse une image avec Albert Vision (albert-large).

    - **image_url**: URL de l'image à analyser (formats: PNG, JPG, WebP)
    - **prompt**: Question ou instruction concernant l'image
    - **system_prompt**: Optionnel - Guide l'analyse
    - **temperature**: Contrôle la créativité (0.0 = déterministe)
    - **max_tokens**: Longueur maximale de la réponse

    Exemples d'utilisation:
    - Décrire une image: "Décris cette image en détail"
    - Extraire du texte: "Quel texte est visible sur cette image ?"
    - Analyser un graphique: "Quelles tendances vois-tu dans ce graphique ?"
    - Identifier des objets: "Quels objets sont présents sur cette photo ?"
    """
    start_time = time.time()

    try:
        # Créer un client LLM avec albert-large
        import os
        api_key = os.getenv("DEFAULT_LLM_API_KEY", "")
        base_url = os.getenv("DEFAULT_LLM_BASE_URL", "https://albert.api.etalab.gouv.fr")

        if not api_key:
            return VisionResponse.from_error(
                image_url=str(request.image_url),
                prompt=request.prompt,
                error_message="API key not configured"
            )

        llm_client = LLMFactory.create(
            provider="albert",
            api_key=api_key,
            model="albert-large",  # Vision nécessite albert-large
            base_url=base_url
        )

        logger.info(f"Analyzing image: {request.image_url}")

        # Analyser l'image avec Albert Vision
        analysis = await llm_client.generate_with_vision(
            text=request.prompt,
            image_url=str(request.image_url),
            system_prompt=request.system_prompt,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )

        processing_time = time.time() - start_time

        logger.info(f"Vision analysis completed in {processing_time:.2f}s")

        return VisionResponse(
            success=True,
            image_url=str(request.image_url),
            prompt=request.prompt,
            analysis=analysis,
            model_used="albert-large",
            processing_time_seconds=round(processing_time, 2)
        )

    except Exception as e:
        logger.error(f"Error analyzing image: {e}")
        processing_time = time.time() - start_time

        response = VisionResponse.from_error(
            image_url=str(request.image_url),
            prompt=request.prompt,
            error_message=str(e)
        )
        response.processing_time_seconds = round(processing_time, 2)
        return response
