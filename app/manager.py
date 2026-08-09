"""
Manager principal pour orchestrer les extractions web.
"""

import logging
from typing import Optional, Dict, Any

from app.api.models import WebResult, ExtractionOptions
from app.core.config import settings
from app.core.llm.base import BaseLLMClient
from app.core.llm.factory import LLMFactory
from app.extractors.direct_extractor import DirectExtractor
from app.extractors.agent_extractor import AgentExtractor
from app.extractors.rss_extractor import RssExtractor
from app.utils.url_safety import assert_safe_url, UnsafeURLError
from app.utils.content_detector import ContentDetector
from app.utils.prompts import PromptTemplates

logger = logging.getLogger(__name__)


class ExtractorManager:
    """
    Manager principal qui coordonne les différents extractors.
    """

    def __init__(self, cache_ttl_seconds: int = 300):
        """Initialise le manager avec les extractors disponibles."""
        self.rss_extractor = RssExtractor()
        self.direct_extractor = DirectExtractor()
        self.agent_extractor = AgentExtractor()
        self.content_detector = ContentDetector()

        # Cache d'extraction avec TTL, partage par toute la duree de vie de
        # cette instance de manager (donc entre requetes API successives si
        # le manager est reutilise, comme c'est le cas pour /extract et pour
        # IntelligentOrchestrator depuis la correction ci-dessus). Une meme
        # URL redemandee peu apres evite de refaire tout le travail
        # d'extraction (navigateur, LLM eventuel).
        self._cache: dict = {}
        self._cache_ttl = cache_ttl_seconds

    async def extract(
        self,
        url: str,
        prompt: Optional[str] = None,
        extraction_type: str = "general",
        llm_client: Optional[BaseLLMClient] = None,
        options: Optional[ExtractionOptions] = None
    ) -> WebResult:
        """
        Point d'entrée principal pour l'extraction de contenu web.

        Args:
            url: URL à extraire
            prompt: Prompt personnalisé (optionnel)
            extraction_type: Type d'extraction ("general", "article", "product", etc.)
            llm_client: Client LLM à utiliser (optionnel, utilise config par défaut si non fourni)
            options: Options d'extraction

        Returns:
            WebResult avec le contenu extrait
        """
        logger.info(f"Début extraction de {url} (type: {extraction_type})")

        # Protection SSRF : verifiee ici, au point d'entree unique de toute
        # extraction (RSS, direct, LLM leger, agent en heritent tous), avant
        # toute tentative reelle de connexion.
        try:
            assert_safe_url(url)
        except UnsafeURLError as e:
            logger.warning(f"Extraction refusee pour {url}: {e}")
            return WebResult.from_error(url=url, error_message=str(e))

        # Cache avec TTL : une meme URL redemandee dans la fenetre de
        # validite evite de refaire tout le travail (navigateur, LLM).
        import time
        cache_key = f"{url}::{extraction_type}"
        cached = self._cache.get(cache_key)
        if cached:
            cached_result, cached_at = cached
            if time.time() - cached_at < self._cache_ttl:
                logger.info(f"Cache hit pour {url} (age: {int(time.time() - cached_at)}s)")
                return cached_result
            else:
                del self._cache[cache_key]  # perime, purge

        # Détection automatique du type si "general"
        if extraction_type == "general":
            detected_type = self.content_detector.detect(url)
            logger.info(f"Type détecté automatiquement: {detected_type}")
            extraction_type = detected_type

        # Générer le prompt approprié
        final_prompt = PromptTemplates.get_prompt(
            content_type=extraction_type,
            url=url,
            custom_prompt=prompt
        )

        logger.debug(f"Prompt utilisé: {final_prompt[:200]}...")

        # PALIER 0 : tenter un flux RSS/Atom avant tout usage de navigateur ou d'IA.
        opts_check = options or ExtractionOptions()
        if getattr(opts_check, "try_rss_first", True):
            try:
                rss_result = await self.rss_extractor.try_extract(url)
                if rss_result and rss_result.success:
                    logger.info(f"Extraction via flux RSS reussie pour {url}")
                    return rss_result
            except Exception as e:
                logger.debug(f"Palier RSS non concluant pour {url}: {e}")

        # Options par défaut
        opts = options or ExtractionOptions()
        extraction_options = {
            "timeout": opts.timeout,
            "headless": opts.headless,
            "use_fallback": True
        }

        # Créer le client LLM par défaut si non fourni
        if not llm_client and opts.use_agent:
            try:
                llm_client = await self._create_default_llm_client()
            except Exception as e:
                logger.error(f"Impossible de créer le client LLM par défaut: {e}")
                # Fallback vers extraction directe
                opts.use_agent = False

        # Sélectionner l'extractor approprié
        if opts.use_agent and llm_client:
            logger.info("Utilisation de l'agent extractor")
            result = await self.agent_extractor.extract(
                url=url,
                prompt=final_prompt,
                llm_client=llm_client,
                options=extraction_options
            )
        else:
            logger.info("Utilisation de l'extractor direct")
            result = await self.direct_extractor.extract(
                url=url,
                prompt=final_prompt,
                llm_client=None,
                options=extraction_options
            )

        # Ajouter le type de contenu au résultat
        if result.success and not result.content_type:
            result.content_type = extraction_type

        logger.info(
            f"Extraction terminée: success={result.success}, "
            f"content_length={len(result.content) if result.content else 0}"
        )

        # N'a mettre en cache que les succes : un echec transitoire (page
        # temporairement indisponible) ne doit pas etre fige pour 5 minutes.
        if result.success:
            self._cache[cache_key] = (result, time.time())

        return result

    async def _create_default_llm_client(self) -> BaseLLMClient:
        """
        Crée un client LLM avec la configuration par défaut.

        Returns:
            Instance de BaseLLMClient

        Raises:
            Exception: Si la configuration est invalide
        """
        provider = settings.default_llm_provider
        api_key = ""
        base_url = settings.default_llm_base_url

        # Récupérer la clé API selon le provider
        if provider == "openai":
            api_key = settings.openai_api_key or settings.default_llm_api_key
        elif provider == "anthropic":
            api_key = settings.anthropic_api_key or settings.default_llm_api_key
        elif provider == "albert":
            api_key = settings.albert_api_key or settings.default_llm_api_key
            base_url = settings.albert_api_url
        else:
            api_key = settings.default_llm_api_key

        if not api_key:
            raise ValueError(
                f"Clé API manquante pour le provider {provider}. "
                f"Configurez la variable d'environnement appropriée."
            )

        return LLMFactory.create(
            provider=provider,
            api_key=api_key,
            model=settings.default_llm_model,
            base_url=base_url
        )
