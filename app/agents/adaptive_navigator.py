"""
AdaptiveNavigator - navigation adaptative pour l'orchestrateur intelligent.

Reconstruit a partir de l'usage reel observe dans intelligent_orchestrator.py
(fichier source manquant dans le depot d'origine). S'appuie sur la chaine
d'extraction deja durcie (RSS -> direct+trafilatura -> LLM unique -> agent
complet en dernier recours) plutot que de redevelopper une logique de
navigation separee.
"""

import logging
from enum import Enum
from typing import Any, Dict, Optional

from app.manager import ExtractorManager
from app.core.llm.base import BaseLLMClient
from app.api.models import ExtractionOptions

logger = logging.getLogger(__name__)


class StrategyType(str, Enum):
    """
    Strategies de source, telles que documentees pour /research/quick et
    /research/deep. Conservees ici pour compatibilite ascendante avec le
    code appelant, meme si l'usage actif reste a construire au niveau
    orchestrateur (pas de logique de filtrage de domaine cablee ici).
    """
    OPEN = "open"                # recherche web ouverte
    PRIORITY = "priority"        # sources fiables prioritaires + web si incomplet
    EXCLUSIVE = "exclusive"      # uniquement les sources fournies, pas de web
    COMPLEMENT = "complement"    # web + ajout d'URLs specifiques
    WHITELIST = "whitelist"      # limiter aux domaines de confiance
    BLACKLIST = "blacklist"      # exclure des domaines


class AdaptiveNavigator:
    """
    Navigue vers une URL cible et en extrait un contenu pertinent pour une
    requete utilisateur donnee, en s'appuyant sur ExtractorManager (qui
    applique deja l'escalade RSS -> direct -> LLM unique -> agent).
    """

    def __init__(self, llm_client: Optional[BaseLLMClient] = None, timeout: int = 45):
        self.llm_client = llm_client
        self.timeout = timeout
        self.manager = ExtractorManager()

    async def execute(
        self,
        user_query: str,
        target_url: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Extrait le contenu de target_url en tenant compte de user_query.

        Returns:
            {"success": bool, "results": [...], "error": str | None}
        """
        if not target_url:
            return {"success": False, "results": None, "error": "target_url manquant"}

        context = context or {}

        try:
            result = await self.manager.extract(
                url=target_url,
                prompt=user_query,
                extraction_type="general",
                llm_client=self.llm_client,
                options=ExtractionOptions(timeout=self.timeout)
            )
        except Exception as e:
            logger.error(f"AdaptiveNavigator: echec extraction {target_url}: {e}")
            return {"success": False, "results": None, "error": str(e)}

        if not result.success:
            return {"success": False, "results": None, "error": result.error}

        return {
            "success": True,
            "results": [{
                "url": target_url,
                "title": result.title,
                "content": result.content,
                "extraction_method": (result.metadata or {}).get("extraction_method"),
            }],
            "error": None
        }

    async def close(self):
        """Nettoyage - rien a fermer explicitement, ExtractorManager gere ses propres ressources par appel."""
        pass
