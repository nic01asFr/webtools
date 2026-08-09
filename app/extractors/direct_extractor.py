"""
Extractor direct utilisant Playwright sans agent IA.
Rapide mais limité pour le contenu dynamique.
"""

import re
import logging
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from .base import BaseExtractor, ExtractionError
from app.api.models import WebResult
from app.core.llm.base import BaseLLMClient
from app.core.browser.playwright_manager import ensure_playwright_installed
from app.core.content_cleaner import AdvancedContentCleaner

logger = logging.getLogger(__name__)


class DirectExtractor(BaseExtractor):
    """
    Extractor utilisant Playwright directement sans agent IA.
    Bon pour les sites statiques ou peu dynamiques.
    """

    def __init__(self):
        """Initialise l'extracteur avec le nettoyeur de contenu."""
        self.content_cleaner = AdvancedContentCleaner()

    async def extract(
        self,
        url: str,
        prompt: str,
        llm_client: Optional[BaseLLMClient] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> WebResult:
        """
        Extrait le contenu avec Playwright direct.

        Args:
            url: URL à extraire
            prompt: Prompt (ignoré pour l'extraction directe)
            llm_client: Client LLM (non utilisé pour l'extraction directe)
            options: Options d'extraction

        Returns:
            WebResult avec le contenu extrait
        """
        # Vérifier que Playwright est disponible
        if not await ensure_playwright_installed():
            return WebResult.from_error(
                url=url,
                error_message="Playwright n'est pas disponible"
            )

        # Options par défaut
        opts = options or {}
        timeout = opts.get("timeout", 45) * 1000  # Convertir en millisecondes
        headless = opts.get("headless", True)

        try:
            async with async_playwright() as p:
                # Lancer le navigateur
                browser = await p.chromium.launch(
                    headless=headless,
                    args=['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage']
                )

                # Créer un contexte
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

                page = await context.new_page()

                try:
                    # Naviguer vers l'URL
                    logger.info(f"Navigation vers {url}")
                    await page.goto(url, timeout=timeout, wait_until="domcontentloaded")

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
                        page_html, url=url, with_metadata=False,
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

                finally:
                    await page.close()
                    await context.close()
                    await browser.close()

        except PlaywrightTimeout:
            logger.error(f"Timeout lors de l'extraction de {url}")
            return WebResult.from_error(
                url=url,
                error_message=f"Timeout lors de l'accès à l'URL (> {timeout}ms)"
            )

        except Exception as e:
            logger.error(f"Erreur lors de l'extraction directe de {url}: {e}")
            return WebResult.from_error(
                url=url,
                error_message=f"Erreur lors de l'extraction: {str(e)}"
            )

    def _clean_content(self, content: str) -> str:
        """
        Nettoie le contenu extrait.

        Args:
            content: Contenu brut

        Returns:
            Contenu nettoyé
        """
        if not content:
            return ""

        # Supprimer les lignes vides multiples
        content = re.sub(r'\n\s*\n', '\n\n', content)

        # Supprimer les espaces multiples
        content = re.sub(r' +', ' ', content)

        # Nettoyer les lignes
        lines = content.split('\n')
        cleaned_lines = [line.strip() for line in lines if line.strip()]

        return '\n'.join(cleaned_lines)
