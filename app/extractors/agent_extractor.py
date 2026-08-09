"""
Extractor utilisant browser-use avec agent IA.
Adapté depuis Colaig pour être autonome et multi-LLM.
"""

import re
import asyncio
import logging
from typing import Dict, Any, Optional

from browser_use import Agent

from .base import BaseExtractor, ExtractionError
from .direct_extractor import DirectExtractor
from app.api.models import WebResult
from app.core.llm.base import BaseLLMClient
from app.core.browser.playwright_manager import ensure_playwright_installed

logger = logging.getLogger(__name__)


class AgentExtractor(BaseExtractor):
    """
    Extractor utilisant browser-use avec un agent IA.
    Plus puissant que l'extraction directe, capable de gérer du contenu dynamique
    et d'interagir intelligemment avec les pages.
    """

    def __init__(self):
        """Initialise l'extractor avec agent."""
        self.direct_extractor = DirectExtractor()

    async def extract(
        self,
        url: str,
        prompt: str,
        llm_client: Optional[BaseLLMClient] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> WebResult:
        """
        Extrait le contenu avec un agent IA (browser-use).

        Args:
            url: URL à extraire
            prompt: Prompt pour guider l'agent
            llm_client: Client LLM à utiliser (requis pour l'agent)
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

        # Le client LLM est requis pour l'agent
        if not llm_client:
            return WebResult.from_error(
                url=url,
                error_message="Client LLM requis pour l'extraction avec agent"
            )

        # Options par défaut
        opts = options or {}
        timeout = opts.get("timeout", 45)
        use_fallback = opts.get("use_fallback", True)

        try:
            # ÉTAPE 1: Essayer l'extraction directe d'abord (plus rapide)
            logger.info(f"Tentative d'extraction directe pour {url}")
            direct_result = await self.direct_extractor.extract(url, prompt, None, options)

            # Si l'extraction directe a produit un contenu substantiel, l'utiliser
            if direct_result.success and direct_result.content and len(direct_result.content) > 500:
                logger.info(f"Extraction directe réussie: {len(direct_result.content)} caractères")
                direct_result.metadata["extraction_method"] = "direct_playwright_fast_path"
                return direct_result

            # ÉTAPE 1.5 : si l'extraction directe a produit un contenu partiel (pas vide,
            # juste insuffisant/mal structure), un SEUL appel LLM sur ce texte deja
            # nettoye coute des dizaines de fois moins cher qu'un agent qui re-navigue
            # la page depuis zero. On ne reserve l'agent complet qu'aux cas ou la page
            # n'a produit litteralement aucun contenu exploitable.
            if direct_result.success and direct_result.content and len(direct_result.content) >= 100:
                logger.info(f"Contenu partiel ({len(direct_result.content)} car.) - tentative d'un appel LLM unique (non-agentique) avant l'agent complet")
                try:
                    raw_text = direct_result.content[:8000]  # borne large mais raisonnable
                    llm_response = await llm_client.generate(messages=[
                        {"role": "system", "content": "Tu structures du texte web deja extrait. Reponds UNIQUEMENT au format TITRE: ... puis CONTENU: ..., sans explication."},
                        {"role": "user", "content": f"Texte extrait de {url} :\n\n{raw_text}"}
                    ])
                    if llm_response and len(llm_response) > 100:
                        title_match = re.search(r'TITRE:\s*(.*?)(?:\n|$)', llm_response, re.IGNORECASE)
                        content_match = re.search(r'CONTENU:\s*(.*)', llm_response, re.IGNORECASE | re.DOTALL)
                        title = title_match.group(1).strip() if title_match else (direct_result.title or "Contenu extrait")
                        cleaned = content_match.group(1).strip() if content_match else llm_response
                        logger.info(f"Palier LLM unique reussi: {len(cleaned)} caracteres, sans agent complet")
                        return WebResult.from_success(
                            url=url, content_type="webpage", title=title, content=cleaned,
                            metadata={"extraction_method": "direct_playwright_single_llm_pass"}
                        )
                except Exception as e:
                    logger.warning(f"Palier LLM unique echoue pour {url}, escalade vers agent complet: {e}")

            # ÉTAPE 2: Utiliser l'agent IA (dernier recours - page vide/inaccessible en direct)
            logger.info(f"Extraction avec agent IA pour {url}")

            # Obtenir le wrapper LangChain du client LLM
            llm_wrapper = llm_client.get_langchain_wrapper()

            # Créer l'agent browser-use
            agent = Agent(
                task=prompt,
                llm=llm_wrapper,
                use_vision=False,
                max_actions_per_step=3
            )

            # Exécuter l'agent avec timeout
            extraction_task = asyncio.create_task(agent.run(max_steps=8))

            try:
                result = await asyncio.wait_for(extraction_task, timeout=float(timeout))

                # Extraire le contenu de la réponse de l'agent
                processed_content = await self._extract_from_agent_response(result)

                # Vérifier que la tâche de l'agent s'est réellement terminée avec succès
                # (et pas juste qu'il y a du texte : un message d'échec a aussi du texte)
                agent_succeeded = result.is_successful()
                if agent_succeeded is False:
                    preview = (processed_content or "")[:200]
                    logger.warning(f"Agent a terminé en échec explicite pour {url}: {preview}")
                    return WebResult.from_error(
                        url=url,
                        error_message=processed_content[:500] if processed_content else "Agent a signalé un échec (page bloquée, inaccessible, ou contenu introuvable)"
                    )

                # Vérifier la qualité du contenu
                if agent_succeeded and processed_content and len(processed_content) > 100:
                    # Analyser le contenu pour extraire titre et corps
                    title_match = re.search(
                        r'TITRE:\s*(.*?)(?:\n|$)',
                        processed_content,
                        re.IGNORECASE
                    )
                    content_match = re.search(
                        r'CONTENU:\s*(.*)',
                        processed_content,
                        re.IGNORECASE | re.DOTALL
                    )

                    if title_match and content_match:
                        title = title_match.group(1).strip()
                        content = content_match.group(1).strip()
                    else:
                        # Fallback: utiliser le contenu brut
                        title = "Contenu extrait"
                        content = processed_content

                    logger.info(f"Extraction avec agent réussie: {len(content)} caractères")

                    return WebResult.from_success(
                        url=url,
                        content_type="webpage",
                        title=title,
                        content=content,
                        metadata={
                            "extraction_method": "agent_browser_use",
                            "content_length": len(content),
                            "llm_provider": type(llm_client).__name__
                        }
                    )

                else:
                    logger.warning(f"Contenu extrait par agent insuffisant: {len(processed_content) if processed_content else 0} caractères")

                    # Fallback vers extraction directe si configuré
                    if use_fallback and direct_result.success:
                        logger.info("Utilisation du résultat de l'extraction directe en fallback")
                        return direct_result

                    return WebResult.from_error(
                        url=url,
                        error_message="Contenu extrait par agent insuffisant"
                    )

            except asyncio.TimeoutError:
                logger.error(f"Timeout de l'agent après {timeout}s pour {url}")

                # Fallback vers extraction directe
                if use_fallback and direct_result.success:
                    logger.info("Utilisation du résultat de l'extraction directe après timeout agent")
                    return direct_result

                return WebResult.from_error(
                    url=url,
                    error_message=f"Timeout de l'agent après {timeout}s"
                )

        except Exception as e:
            logger.error(f"Erreur lors de l'extraction avec agent de {url}: {e}")

            # Fallback vers extraction directe en cas d'erreur
            if use_fallback:
                try:
                    logger.info("Tentative de fallback vers extraction directe après erreur agent")
                    direct_result = await self.direct_extractor.extract(url, prompt, None, options)
                    if direct_result.success:
                        return direct_result
                except Exception as fallback_error:
                    logger.error(f"Échec du fallback: {fallback_error}")

            return WebResult.from_error(
                url=url,
                error_message=f"Erreur lors de l'extraction avec agent: {str(e)}"
            )

    async def _extract_from_agent_response(self, response: Any) -> str:
        """
        Extrait le contenu textuel de la réponse de l'agent browser-use.

        Args:
            response: Réponse de l'agent

        Returns:
            Contenu extrait sous forme de texte
        """
        try:
            # La réponse peut être de différents types selon la version de browser-use
            if isinstance(response, str):
                return response

            # AgentHistoryList (browser-use) expose final_result() qui renvoie
            # le texte du dernier "done" - PAS un attribut .text/.content brut.
            # Sans ce cas prioritaire, le code tombait sur str(response) plus
            # bas, qui serialise TOUT l'historique interne de l'agent (chaque
            # etape, all_model_outputs...) au lieu du seul texte final -
            # pollution massive du titre/contenu retourne au client.
            if hasattr(response, 'final_result'):
                final = response.final_result()
                if final:
                    return str(final)

            # Si c'est un objet avec attribut text ou content
            if hasattr(response, 'text'):
                return str(response.text)

            if hasattr(response, 'content'):
                return str(response.content)

            # Si c'est une liste de messages
            if isinstance(response, list):
                contents = []
                for item in response:
                    if isinstance(item, dict) and 'content' in item:
                        contents.append(str(item['content']))
                    elif hasattr(item, 'content'):
                        contents.append(str(item.content))
                    elif isinstance(item, str):
                        contents.append(item)

                return '\n'.join(contents)

            # Fallback: conversion en string
            return str(response)

        except Exception as e:
            logger.error(f"Erreur lors de l'extraction du contenu de la réponse: {e}")
            return ""
