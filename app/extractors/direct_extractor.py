"""
Extractor direct utilisant Playwright sans agent IA.
Rapide mais limité pour le contenu dynamique.
"""

import logging
from contextlib import asynccontextmanager
from typing import Dict, Any
from collections.abc import Callable, AsyncIterator
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from .base import BaseExtractor
from app.api.models import WebResult
from app.core.llm.base import BaseLLMClient
from app.core.browser.playwright_manager import ensure_playwright_installed, install_ssrf_route
from app.utils.url_safety import Resolver

logger = logging.getLogger(__name__)

# Fabrique de page : page_factory(headless=bool) -> gestionnaire de contexte
# asynchrone qui rend une page Playwright (ou un double de test).
PageFactory = Callable[..., Any]

# Balises en ligne dont trafilatura 2.3 jette le texte. Mesure citecheck du
# 13/11/2026 : Wikipedia balise ses dates en <time>, et "Son lancement a lieu
# le 25 décembre 2021" devenait "Son lancement a lieu le au centre spatial",
# d'ou une reponse fausse ("les sources ne donnent pas la date").
_DROPPED_INLINE_TAGS = ("time", "output")


def unwrap_dropped_inline(page_html: str) -> str:
    """Retire les balises que trafilatura supprime, en gardant leur texte."""
    import lxml.etree
    import lxml.html

    try:
        tree = lxml.html.fromstring(page_html)
    except (lxml.etree.ParserError, ValueError):
        return page_html
    lxml.etree.strip_tags(tree, *_DROPPED_INLINE_TAGS)
    return lxml.html.tostring(tree, encoding="unicode")


@asynccontextmanager
async def _playwright_page(headless: bool = True) -> AsyncIterator[Any]:
    """Page Chromium reelle ; navigateur et contexte fermes a la sortie."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage']
        )
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 1080},
                # Identite navigateur humain standard : se fondre dans le trafic
                # normal plutot que de se declarer bot (les systemes anti-bot
                # mettent en liste blanche des bots partenaires connus, pas les
                # bots inconnus - un Chrome standard evite de se signaler d'emblee).
                # Ne change rien aux regles de securite : jamais de CAPTCHA force,
                # jamais de contenu de substitution si l'acces est refuse malgre tout.
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="fr-FR",
                timezone_id="Europe/Paris"
            )
            try:
                page = await context.new_page()
                try:
                    yield page
                finally:
                    await page.close()
            finally:
                await context.close()
        finally:
            await browser.close()


class DirectExtractor(BaseExtractor):
    """
    Extractor utilisant Playwright directement sans agent IA.
    Bon pour les sites statiques ou peu dynamiques.
    """

    async def extract(
        self,
        url: str,
        prompt: str,
        llm_client: BaseLLMClient | None = None,
        options: Dict[str, Any] | None = None,
        *,
        page_factory: PageFactory | None = None,
        resolver: Resolver | None = None,
    ) -> WebResult:
        """
        Extrait le contenu avec Playwright direct.

        Args:
            url: URL à extraire
            prompt: Prompt (ignoré pour l'extraction directe)
            llm_client: Client LLM (non utilisé pour l'extraction directe)
            options: Options d'extraction
            page_factory: fabrique de page injectable (tests) ; par defaut,
                Chromium via Playwright
            resolver: resolveur DNS injectable pour la garde SSRF (tests)

        Returns:
            WebResult avec le contenu extrait
        """
        if page_factory is None:
            # Vérifier que Playwright est disponible
            if not await ensure_playwright_installed():
                return WebResult.from_error(
                    url=url,
                    error_message="Playwright n'est pas disponible"
                )
            page_factory = _playwright_page

        # Options par défaut
        opts = options or {}
        timeout = opts.get("timeout", 45) * 1000  # Convertir en millisecondes
        headless = opts.get("headless", True)

        blocked: list[str] = []
        try:
            async with page_factory(headless=headless) as page:
                # Filtre SSRF sur toutes les requetes de la page : navigation,
                # redirections et sous-ressources.
                blocked = await install_ssrf_route(page, resolver)

                # Naviguer vers l'URL
                logger.info(f"Navigation vers {url}")
                response = await page.goto(url, timeout=timeout, wait_until="domcontentloaded")

                # Une page d'erreur HTTP n'est pas un contenu.
                status = getattr(response, "status", None) if response is not None else None
                if isinstance(status, int) and status >= 400:
                    return WebResult.from_error(
                        url=url,
                        error_message=f"Erreur HTTP {status} lors de l'accès à l'URL"
                    )

                # Attendre un peu pour le contenu dynamique
                await page.wait_for_timeout(2000)

                # Extraire le HTML complet de la page
                page_html = await page.content()

                # Nettoyage via trafilatura : bibliotheque eprouvee specialisee
                # dans l'extraction d'article principal (elimine bien mieux le bruit
                # de nav/pub/footer qu'un nettoyeur maison), et surtout produit un
                # texte beaucoup plus compact -> moins de tokens a envoyer a tout
                # LLM en aval, sans rien perdre du contenu utile.
                import trafilatura
                content = trafilatura.extract(
                    unwrap_dropped_inline(page_html), url=url, with_metadata=False,
                    include_comments=False, include_tables=True
                )
                traf_meta = trafilatura.extract_metadata(page_html)
                title = (traf_meta.title if traf_meta else None) or await page.title()

                # Vérifier si on a du contenu
                if not content or len(content) < 100:
                    return WebResult.from_error(
                        url=url,
                        error_message="Contenu extrait insuffisant (< 100 caractères)"
                    )

                logger.info(f"Extraction directe (trafilatura) réussie: {len(content)} chars")

                metadata = {
                    "extraction_method": "direct_playwright_trafilatura",
                    "content_length": len(content),
                }
                if isinstance(status, int):
                    metadata["http_status"] = status

                # Ajouter les métadonnées extraites
                if traf_meta:
                    if traf_meta.author:
                        metadata["author"] = traf_meta.author
                    if traf_meta.date:
                        metadata["publish_date"] = traf_meta.date
                    if traf_meta.description:
                        metadata["description"] = traf_meta.description

                return WebResult.from_success(
                    url=url,
                    content_type="webpage",
                    title=title,
                    content=content,
                    metadata=metadata
                )

        except PlaywrightTimeout:
            logger.error(f"Timeout lors de l'extraction de {url}")
            return WebResult.from_error(
                url=url,
                error_message=f"Timeout lors de l'accès à l'URL (> {timeout}ms)"
            )

        except Exception as e:
            if blocked:
                logger.warning(f"Navigation refusee pour {url}: requete bloquee {blocked[0]}")
                return WebResult.from_error(
                    url=url,
                    error_message=f"L'URL cible une ressource réseau interne ({blocked[0]})"
                )
            logger.error(f"Erreur lors de l'extraction directe de {url}: {e}")
            return WebResult.from_error(
                url=url,
                error_message=f"Erreur lors de l'extraction: {str(e)}"
            )
