"""
Manager principal pour orchestrer les extractions web.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Optional, Dict, Any
from urllib.parse import urlparse, parse_qs

from app.api.models import WebResult, ExtractionOptions
from app.core.config import settings
from app.core.llm.base import BaseLLMClient
from app.core.llm.factory import LLMFactory
from app.extractors.direct_extractor import DirectExtractor
from app.extractors.agent_extractor import AgentExtractor
from app.extractors.rss_extractor import RssExtractor, COMMON_FEED_PATHS
from app.extractors.pdf_extractor import PdfExtractor
from app.utils.url_safety import assert_safe_url, UnsafeURLError
from app.utils.content_detector import ContentDetector
from app.utils.prompts import PromptTemplates

logger = logging.getLogger(__name__)

# Nombre maximal d'entrees conservees dans le cache d'extraction.
DEFAULT_CACHE_MAX_ENTRIES = 256

# Marge accordee a un palier au-dela du budget restant, pour qu'un palier qui
# gere lui-meme son delai (agent) puisse rendre son repli avant l'interruption.
BUDGET_GRACE_SECONDS = 1.0

_FEED_SUFFIXES = (".xml", ".rss", ".atom")


def is_site_root_or_feed(url: str) -> bool:
    """Vrai si l'URL est la racine du site ou un flux RSS/Atom lui-meme."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    query = parse_qs(parsed.query)
    if path == "/" and not query:
        return True
    if path.rstrip("/") in COMMON_FEED_PATHS or path.lower().endswith(_FEED_SUFFIXES):
        return True
    return "feed" in query


class ExtractorManager:
    """
    Manager principal qui coordonne les différents extractors.
    """

    def __init__(
        self,
        cache_ttl_seconds: int | None = None,
        enable_caching: bool | None = None,
        cache_max_entries: int = DEFAULT_CACHE_MAX_ENTRIES,
    ):
        """
        Initialise le manager avec les extractors disponibles.

        cache_ttl_seconds / enable_caching : pris de la configuration
        (app.core.config) si non fournis explicitement. Ces reglages etaient
        declares dans la config mais jamais lus - le TTL reel etait code en
        dur ici, donc changer CACHE_TTL_SECONDS en production n'avait aucun
        effet observable.
        """
        from app.core.config import settings
        self.pdf_extractor = PdfExtractor()
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
        self._cache: OrderedDict = OrderedDict()
        self._cache_max_entries = cache_max_entries
        self._clock = time.monotonic
        self._cache_ttl = cache_ttl_seconds if cache_ttl_seconds is not None else settings.cache_ttl_seconds
        self._cache_enabled = enable_caching if enable_caching is not None else settings.enable_caching

    @staticmethod
    def _make_cache_key(
        url: str,
        extraction_type: str,
        prompt: str | None,
        options: ExtractionOptions,
        has_llm_client: bool,
    ) -> str:
        """Cle de cache couvrant tout ce qui change le resultat."""
        payload = json.dumps(
            [url, extraction_type, prompt, options.model_dump(), has_llm_client],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> WebResult | None:
        if not self._cache_enabled:
            return None
        cached = self._cache.get(key)
        if not cached:
            return None
        cached_result, cached_at = cached
        if time.time() - cached_at < self._cache_ttl:
            logger.info(f"Cache hit (age: {int(time.time() - cached_at)}s)")
            self._cache.move_to_end(key)
            # Copie : l'appelant peut modifier l'objet sans corrompre le cache.
            return cached_result.model_copy(deep=True)
        del self._cache[key]
        return None

    def _cache_put(self, key: str, result: WebResult) -> None:
        # Seuls les succes sont conserves : un echec transitoire ne doit pas
        # etre fige pour la duree du TTL.
        if not (result.success and self._cache_enabled):
            return
        self._cache[key] = (result.model_copy(deep=True), time.time())
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max_entries:
            self._cache.popitem(last=False)

    @staticmethod
    def _budget_error(url: str, budget: float) -> WebResult:
        return WebResult.from_error(
            url=url,
            error_message=f"Budget d'extraction epuise ({budget:g}s, tous paliers confondus)",
        )

    async def extract(
        self,
        url: str,
        prompt: Optional[str] = None,
        extraction_type: str = "general",
        llm_client: Optional[BaseLLMClient] = None,
        options: Optional[ExtractionOptions] = None
    ) -> WebResult:
        """
        Point d'entree principal pour l'extraction de contenu web.

        options.timeout est un budget global : la somme des paliers (PDF, RSS,
        direct, LLM, agent) ne depasse pas cette duree (a BUDGET_GRACE_SECONDS
        pres). Le resultat porte le palier producteur dans
        metadata["extraction_tier"] (pdf, rss, direct, llm, agent).
        """
        logger.info(f"Debut extraction de {url} (type: {extraction_type})")
        opts = options.model_copy() if options else ExtractionOptions()
        budget = float(opts.timeout)
        deadline = self._clock() + budget

        # Protection SSRF : verifiee ici, au point d'entree unique de toute
        # extraction, avant toute tentative reelle de connexion.
        try:
            assert_safe_url(url)
        except UnsafeURLError as e:
            logger.warning(f"Extraction refusee pour {url}: {e}")
            return WebResult.from_error(url=url, error_message=str(e))

        cache_key = self._make_cache_key(url, extraction_type, prompt, opts, llm_client is not None)
        cached_result = self._cache_get(cache_key)
        if cached_result is not None:
            return cached_result

        # Detection automatique du type si "general"
        if extraction_type == "general":
            detected_type = self.content_detector.detect(url)
            logger.info(f"Type detecte automatiquement: {detected_type}")
            extraction_type = detected_type

        final_prompt = PromptTemplates.get_prompt(
            content_type=extraction_type,
            url=url,
            custom_prompt=prompt
        )

        def remaining() -> float:
            return deadline - self._clock()

        # PALIER PDF : un PDF n'est pas une page web, le telechargement direct
        # est la seule voie correcte et passe avant tout le reste.
        try:
            pdf_result = await asyncio.wait_for(
                self.pdf_extractor.try_extract(url), timeout=max(remaining(), 0.001)
            )
            if pdf_result is not None:
                pdf_result.metadata["extraction_tier"] = "pdf"
                if pdf_result.success:
                    logger.info(f"Extraction PDF directe reussie pour {url}")
                    self._cache_put(cache_key, pdf_result)
                return pdf_result
        except Exception as e:
            logger.debug(f"Palier PDF non concluant pour {url}: {e}")
        if remaining() <= 0:
            return self._budget_error(url, budget)

        # PALIER RSS : avant tout usage de navigateur ou d'IA. Avec
        # rss_tier_root_only, il ne s'applique qu'a la racine du site ou a un
        # flux : sinon le contenu serait la liste des derniers titres du site,
        # attribuee a l'URL demandee.
        rss_applicable = opts.try_rss_first and (
            not opts.rss_tier_root_only or is_site_root_or_feed(url)
        )
        if rss_applicable:
            try:
                rss_result = await asyncio.wait_for(
                    self.rss_extractor.try_extract(url), timeout=max(remaining(), 0.001)
                )
                if rss_result and rss_result.success:
                    logger.info(f"Extraction via flux RSS reussie pour {url}")
                    rss_result.metadata["extraction_tier"] = "rss"
                    self._cache_put(cache_key, rss_result)
                    return rss_result
            except Exception as e:
                logger.debug(f"Palier RSS non concluant pour {url}: {e}")
            if remaining() <= 0:
                return self._budget_error(url, budget)

        use_agent = opts.use_agent
        if not llm_client and use_agent:
            try:
                llm_client = await asyncio.wait_for(
                    self._create_default_llm_client(), timeout=max(remaining(), 0.001)
                )
            except Exception as e:
                logger.error(f"Impossible de creer le client LLM par defaut: {e}")
                use_agent = False
            if remaining() <= 0:
                return self._budget_error(url, budget)

        # Les paliers suivants recoivent le temps restant, pas le budget initial.
        extraction_options = {
            "timeout": remaining(),
            "headless": opts.headless,
            "use_fallback": True,
            "single_llm_pass_enabled": opts.single_llm_pass_enabled,
        }

        agent_used = bool(use_agent and llm_client)
        try:
            if agent_used:
                logger.info("Utilisation de l'agent extractor")
                result = await asyncio.wait_for(
                    self.agent_extractor.extract(
                        url=url,
                        prompt=final_prompt,
                        llm_client=llm_client,
                        options=extraction_options
                    ),
                    timeout=remaining() + BUDGET_GRACE_SECONDS,
                )
            else:
                logger.info("Utilisation de l'extractor direct")
                result = await asyncio.wait_for(
                    self.direct_extractor.extract(
                        url=url,
                        prompt=final_prompt,
                        llm_client=None,
                        options=extraction_options
                    ),
                    timeout=remaining() + BUDGET_GRACE_SECONDS,
                )
        except TimeoutError:
            logger.warning(f"Budget d'extraction epuise pour {url}")
            return self._budget_error(url, budget)

        if result.success and not result.content_type:
            result.content_type = extraction_type
        if result.success:
            result.metadata.setdefault("extraction_tier", "agent" if agent_used else "direct")

        logger.info(
            f"Extraction terminee: success={result.success}, "
            f"content_length={len(result.content) if result.content else 0}"
        )

        self._cache_put(cache_key, result)
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
