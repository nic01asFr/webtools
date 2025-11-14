"""
Agent pour la recherche interactive sur les sites web.
Utilise browser-use pour interagir avec les formulaires de recherche.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import json

from browser_use import Agent, Browser, BrowserConfig
from langchain_core.messages import SystemMessage
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from app.core.llm.base import BaseLLMClient

logger = logging.getLogger(__name__)


class InteractiveSiteAgent:
    """
    Agent pour effectuer des recherches interactives sur les sites web.
    Capable de détecter les formulaires de recherche, les remplir, et extraire les résultats.
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        timeout: int = 60,
        headless: bool = True
    ):
        """
        Initialise l'agent de recherche interactive.

        Args:
            llm_client: Client LLM pour guider la navigation
            timeout: Timeout en secondes pour l'opération
            headless: Utiliser le navigateur en mode headless
        """
        self.llm_client = llm_client
        self.timeout = timeout
        self.headless = headless

    async def search_site(
        self,
        site_url: str,
        search_query: str,
        max_results: int = 10,
        extract_full_content: bool = False
    ) -> Dict[str, Any]:
        """
        Effectue une recherche interactive sur un site web.

        Args:
            site_url: URL du site web
            search_query: Requête de recherche
            max_results: Nombre maximum de résultats à extraire
            extract_full_content: Si True, extrait le contenu complet de chaque résultat

        Returns:
            Dictionnaire avec les résultats de recherche et métadonnées
        """
        logger.info(f"Début recherche sur {site_url} avec query: {search_query}")
        start_time = datetime.now()

        try:
            # Tenter d'abord avec browser-use + LLM (meilleur pour sites JS complexes)
            logger.info("Tentative avec browser-use + LLM...")
            results_data = await self._search_with_agent(
                site_url, search_query, max_results, extract_full_content
            )

            # Si browser-use échoue, essayer Playwright direct en fallback
            if not results_data.get("search_performed", False):
                logger.info("browser-use a échoué, tentative avec Playwright direct...")
                results_data = await self._search_with_playwright(
                    site_url, search_query, max_results, extract_full_content
                )

            # Calculer le temps d'exécution
            processing_time = (datetime.now() - start_time).total_seconds()

            # Déterminer la méthode d'extraction utilisée
            extraction_method = results_data.get("extraction_method", "playwright-direct")

            return {
                "success": True,
                "results": results_data.get("results", []),
                "total_results_found": results_data.get("total_found", len(results_data.get("results", []))),
                "search_form_detected": results_data.get("form_detected", True),
                "search_performed": results_data.get("search_performed", True),
                "processing_time": processing_time,
                "metadata": {
                    "extraction_method": extraction_method
                }
            }

        except Exception as e:
            logger.error(f"Erreur lors de la recherche sur {site_url}: {e}", exc_info=True)
            processing_time = (datetime.now() - start_time).total_seconds()

            return {
                "success": False,
                "error": str(e),
                "processing_time": processing_time,
                "results": [],
                "total_results_found": 0,
                "search_form_detected": False,
                "search_performed": False
            }

    async def _search_with_playwright(
        self,
        site_url: str,
        search_query: str,
        max_results: int,
        extract_full_content: bool
    ) -> Dict[str, Any]:
        """
        Recherche avec Playwright direct (sans LLM), utilisant des sélecteurs CSS communs.
        Plus rapide et fiable pour les formulaires standard.
        """
        try:
            logger.info(f"Lancement de Playwright pour {site_url}")
            async with async_playwright() as p:
                # Options anti-détection pour contourner Cloudflare et protections
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-features=IsolateOrigins,site-per-process'
                    ]
                )

                # Contexte avec user-agent réaliste
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080},
                    locale='fr-FR',
                    timezone_id='Europe/Paris'
                )

                page = await context.new_page()

                # Masquer les traces d'automatisation (anti-Cloudflare)
                await page.add_init_script('''
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    window.chrome = {runtime: {}};
                ''')

                logger.info(f"Navigation vers {site_url}...")
                # Naviguer vers le site (timeout étendu pour sites lents)
                try:
                    await page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
                    logger.info("Page chargée (domcontentloaded)")
                except PlaywrightTimeout:
                    logger.warning("Timeout domcontentloaded, essai sans wait_until")
                    await page.goto(site_url, timeout=60000)

                await page.wait_for_timeout(10000)  # Attendre Cloudflare + JS (10s)
                logger.info("Recherche du champ de recherche...")

                # Essayer de trouver un champ de recherche avec des sélecteurs communs
                # IDs courants d'abord (plus rapides)
                search_selectors = [
                    '#query',  # Legifrance, sites gouvernementaux
                    '#search',
                    '#q',
                    '#searchInput',  # Wikipedia
                    'input[type="search"]',
                    'input[name="query"]',
                    'input[name="search"]',
                    'input[name="q"]',
                    'input[name*="search" i]',
                    'input[name*="query" i]',
                    'input[placeholder*="recherch" i]',
                    'input[placeholder*="search" i]',
                    'input[id*="search" i]',
                    'input[class*="search" i]',
                    '[role="searchbox"]',
                    '.search-input',
                    'input[type="text"]'  # Dernier recours
                ]

                search_input = None
                # Première tentative: wait_for_selector avec timeout
                for selector in search_selectors[:5]:  # Essayer les 5 premiers sélecteurs avec wait
                    try:
                        element = await page.wait_for_selector(selector, timeout=3000)
                        if element:
                            search_input = element
                            logger.info(f"✓ Champ trouvé (wait): {selector}")
                            break
                    except:
                        continue

                # Seconde tentative: query_selector immédiat (sans wait)
                if not search_input:
                    for selector in search_selectors:
                        try:
                            element = await page.query_selector(selector)
                            if element:
                                search_input = element
                                logger.info(f"✓ Champ trouvé (query): {selector}")
                                break
                        except:
                            continue

                # Troisième tentative: JavaScript evaluate pour Legifrance et sites similaires
                if not search_input:
                    logger.info("Tentative avec JavaScript evaluate...")
                    try:
                        # Chercher un input avec id ou name contenant "query", "search", "q"
                        element = await page.evaluate("""() => {
                            // Chercher par ID exact
                            let input = document.getElementById('query') ||
                                       document.getElementById('search') ||
                                       document.getElementById('q');

                            if (input) return input;

                            // Chercher par name
                            input = document.querySelector('input[name="query"]') ||
                                   document.querySelector('input[name="search"]') ||
                                   document.querySelector('input[name="q"]');

                            if (input) return input;

                            // Chercher tous les inputs visibles de type text ou search
                            const allInputs = Array.from(document.querySelectorAll('input[type="text"], input[type="search"]'));
                            const visibleInput = allInputs.find(inp => {
                                const style = window.getComputedStyle(inp);
                                return style.display !== 'none' && style.visibility !== 'hidden' && inp.offsetParent !== null;
                            });

                            return visibleInput || null;
                        }""")

                        if element:
                            # Utiliser le sélecteur direct
                            search_input = await page.query_selector('input[name="query"]') or await page.query_selector('#query')
                            if search_input:
                                logger.info("✓ Champ trouvé via JavaScript evaluate")
                    except Exception as e:
                        logger.debug(f"JavaScript evaluate failed: {e}")

                if not search_input:
                    # Dernière tentative: obtenir tous les inputs et inspecter
                    logger.warning("Sélecteurs standard échoués, inspection de tous les inputs...")
                    all_inputs = await page.query_selector_all('input')
                    logger.info(f"Nombre total d'inputs trouvés: {len(all_inputs)}")

                    if len(all_inputs) > 0:
                        # Prendre le premier input visible (probablement le champ de recherche principal)
                        for inp in all_inputs:
                            is_visible = await inp.is_visible()
                            if is_visible:
                                search_input = inp
                                logger.info("Premier input visible utilisé comme champ de recherche")
                                break

                if not search_input:
                    logger.warning("Aucun champ de recherche trouvé, même après inspection complète")
                    await browser.close()
                    return {
                        "form_detected": False,
                        "search_performed": False,
                        "results": [],
                        "total_found": 0
                    }

                # Remplir le champ et soumettre
                logger.info(f"Remplissage du champ avec: {search_query}")
                await search_input.fill(search_query)
                await page.wait_for_timeout(500)

                # Essayer de soumettre (Entrée ou bouton)
                logger.info("Soumission du formulaire...")
                try:
                    await search_input.press("Enter")
                    logger.info("Formulaire soumis via Enter")
                except:
                    logger.info("Enter failed, recherche d'un bouton submit...")
                    # Chercher un bouton submit
                    submit_selectors = [
                        'button[type="submit"]',
                        'input[type="submit"]',
                        'button[class*="search"]',
                        'button[aria-label*="search" i]',
                        'button[aria-label*="recherch" i]',
                        'button'  # Tous les boutons en dernier recours
                    ]
                    for selector in submit_selectors:
                        try:
                            submit_btn = await page.query_selector(selector)
                            if submit_btn:
                                await submit_btn.click()
                                logger.info(f"Bouton cliqué: {selector}")
                                break
                        except:
                            continue

                # Attendre les résultats (délai étendu pour Cloudflare sur page de résultats)
                logger.info("Attente du chargement des résultats (Cloudflare + JS)...")
                await page.wait_for_timeout(12000)  # 12 secondes pour Cloudflare
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    logger.info("Page de résultats chargée")
                except:
                    # Continuer même si networkidle timeout
                    logger.warning("Timeout networkidle, continuation...")

                # Extraire les résultats avec LLM
                html_content = await page.content()
                logger.info(f"Contenu HTML récupéré: {len(html_content)} caractères")

                # Utiliser le LLM pour parser les résultats (AVANT de fermer le browser)
                logger.info("Utilisation du LLM pour extraire les résultats...")
                extraction_prompt = f"""Analyse ce HTML de page de résultats de recherche pour la requête "{search_query}".

Extrais les {max_results} premiers résultats de recherche.

Pour chaque résultat, identifie:
- title: Le titre cliquable principal
- url: Le lien href (complet ou relatif)
- snippet: L'extrait/description affichée

Retourne UNIQUEMENT un JSON valide:
{{
  "total_found": nombre_total_visible,
  "results": [
    {{"title": "...", "url": "...", "snippet": "...", "metadata": {{}}}}
  ]
}}

HTML (premiers 8000 chars):
{html_content[:8000]}
"""

                messages = [{"role": "user", "content": extraction_prompt}]

                # Créer un nouveau client LLM (le client partagé peut être fermé par browser-use)
                from app.core.llm import get_llm_client
                fresh_llm_client = await get_llm_client()

                llm_response = await fresh_llm_client.generate(messages, max_tokens=2000)
                logger.info(f"Réponse LLM reçue: {llm_response[:200]}...")

                # Fermer le browser APRÈS l'appel LLM
                await browser.close()

                # Parser la réponse JSON du LLM
                try:
                    # Chercher le bloc JSON dans la réponse
                    start_idx = llm_response.find('{')
                    end_idx = llm_response.rfind('}')
                    if start_idx != -1 and end_idx != -1:
                        json_str = llm_response[start_idx:end_idx + 1]
                        parsed_results = json.loads(json_str)
                        logger.info(f"Résultats parsés: {len(parsed_results.get('results', []))} résultats")

                        return {
                            "form_detected": True,
                            "search_performed": True,
                            "results": parsed_results.get("results", []),
                            "total_found": parsed_results.get("total_found", len(parsed_results.get("results", []))),
                            "extraction_method": "playwright-direct"
                        }
                except Exception as e:
                    logger.error(f"Erreur parsing JSON LLM: {e}")

                return {
                    "form_detected": True,
                    "search_performed": False,
                    "results": [],
                    "total_found": 0
                }

        except Exception as e:
            logger.error(f"Erreur Playwright direct: {e}", exc_info=True)
            return {
                "form_detected": False,
                "search_performed": False,
                "results": [],
                "total_found": 0
            }

    async def _search_with_agent(
        self,
        site_url: str,
        search_query: str,
        max_results: int,
        extract_full_content: bool
    ) -> Dict[str, Any]:
        """
        Recherche avec browser-use + LLM pour les cas complexes.
        """
        try:
            # Créer la tâche pour l'agent
            task = self._build_task(site_url, search_query, max_results, extract_full_content)

            # Configurer browser-use
            browser_config = BrowserConfig(
                headless=self.headless,
                disable_security=True,
                minimum_wait_page_load_time=2.0
            )

            # Initialiser le navigateur et l'agent
            browser = Browser(config=browser_config)

            # Créer le wrapper LangChain pour browser-use
            langchain_llm = self.llm_client.get_langchain_wrapper()

            agent = Agent(
                task=task,
                llm=langchain_llm,
                browser=browser
            )

            # Exécuter la recherche
            result = await agent.run()

            # Parser les résultats
            parsed = self._parse_results(result, max_results)
            parsed["extraction_method"] = "browser-use + albert-code"
            return parsed

        except Exception as e:
            logger.error(f"Erreur browser-use agent: {e}", exc_info=True)
            return {
                "form_detected": False,
                "search_performed": False,
                "results": [],
                "total_found": 0
            }

    def _build_system_prompt(self, search_query: str, max_results: int) -> str:
        """Construit le prompt système pour guider l'agent."""
        return f"""Tu es un agent expert en navigation web et extraction de données.

Ta mission: Effectuer une recherche sur le site web fourni.

ÉTAPES À SUIVRE:

1. **Détecter le formulaire de recherche**
   - Cherche les éléments: input[type="search"], input[name*="search"], input[placeholder*="recherche"]
   - Identifie également les boutons de recherche

2. **Remplir le formulaire**
   - Clique sur le champ de recherche si nécessaire
   - Saisis la requête: "{search_query}"
   - Appuie sur Entrée ou clique sur le bouton de recherche

3. **Attendre les résultats**
   - Attends que la page de résultats charge complètement
   - Identifie la liste des résultats

4. **Extraire {max_results} résultats maximum**
   Pour chaque résultat, extrait:
   - Titre (balise <h2>, <h3>, ou élément cliquable principal)
   - URL (lien href)
   - Snippet (description/extrait du contenu)
   - Métadonnées visibles (date, auteur, catégorie, etc.)

5. **Formater la sortie en JSON**
   Retourne UNIQUEMENT un JSON valide avec cette structure:
   {{
     "form_detected": true/false,
     "search_performed": true/false,
     "total_found": nombre_total,
     "results": [
       {{
         "title": "Titre du résultat",
         "url": "https://...",
         "snippet": "Extrait descriptif...",
         "metadata": {{"date": "...", "type": "..."}}
       }}
     ]
   }}

IMPORTANT:
- Si aucun formulaire n'est trouvé, retourne {{"form_detected": false}}
- Si la recherche échoue, retourne {{"search_performed": false}}
- Extrais exactement ce qui est affiché, sans inventer de contenu
- Limite à {max_results} résultats maximum
"""

    def _build_task(
        self,
        site_url: str,
        search_query: str,
        max_results: int,
        extract_full_content: bool
    ) -> str:
        """Construit la tâche pour l'agent browser-use."""
        task = f"""Visite le site {site_url}, trouve le formulaire de recherche, recherche "{search_query}",
et extrait les {max_results} premiers résultats avec leur titre, URL, et extrait.
"""

        if extract_full_content:
            task += "\nPour chaque résultat, visite également la page et extrait le contenu complet."

        task += """

Retourne UNIQUEMENT un JSON valide au format:
{
  "form_detected": true,
  "search_performed": true,
  "total_found": 15,
  "results": [
    {
      "title": "Titre exact du résultat",
      "url": "https://example.com/page",
      "snippet": "Description affichée dans les résultats",
      "metadata": {"date": "2025-01-01"}
    }
  ]
}
"""
        return task

    def _parse_results(self, agent_result: Any, max_results: int) -> Dict[str, Any]:
        """
        Parse les résultats retournés par l'agent browser-use.

        Args:
            agent_result: Résultat de agent.run()
            max_results: Nombre maximum de résultats à extraire

        Returns:
            Dictionnaire structuré avec les résultats
        """
        try:
            # browser-use retourne généralement le résultat final dans result.final_result()
            # ou dans result lui-même selon la version
            if hasattr(agent_result, 'final_result'):
                final_output = agent_result.final_result()
            elif hasattr(agent_result, 'output'):
                final_output = agent_result.output
            else:
                final_output = str(agent_result)

            # Gérer le cas où final_output est None
            if final_output is None:
                logger.warning("L'agent a retourné None")
                return {
                    "form_detected": False,
                    "search_performed": False,
                    "results": [],
                    "total_found": 0
                }

            logger.info(f"Résultat brut de l'agent: {str(final_output)[:500]}")

            # Tenter de parser le JSON
            import json

            # Si le résultat est déjà un dict
            if isinstance(final_output, dict):
                return final_output

            # Si c'est une string, essayer de parser
            if isinstance(final_output, str):
                # Essayer de trouver un bloc JSON dans la réponse
                start_idx = final_output.find('{')
                end_idx = final_output.rfind('}')

                if start_idx != -1 and end_idx != -1:
                    json_str = final_output[start_idx:end_idx + 1]
                    parsed = json.loads(json_str)
                    return parsed

            # Fallback: retourner une structure de base
            logger.warning("Impossible de parser les résultats en JSON, retour structure vide")
            return {
                "form_detected": False,
                "search_performed": False,
                "results": [],
                "total_found": 0
            }

        except Exception as e:
            logger.error(f"Erreur lors du parsing des résultats: {e}", exc_info=True)
            return {
                "form_detected": False,
                "search_performed": False,
                "results": [],
                "total_found": 0,
                "error": str(e)
            }
